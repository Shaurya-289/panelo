"""The Files library as QML sees it (``Library``): a thin Qt adapter over ``FileLibrary``.

The pure-Python ``FileLibrary`` does the work.  This adds what needs Qt: the change signal
(from a file-system watcher), the clipboard, opening files with the system, the Recycle Bin,
and a worker thread for copies and moves that may take a while.
"""

from __future__ import annotations

import builtins
import os
import struct
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import (
    Property,
    QByteArray,
    QFile,
    QFileSystemWatcher,
    QMimeData,
    QObject,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QGuiApplication

from productivity_app.files.library import FileLibrary

_DROP_EFFECT_COPY: Final = 1
_MAX_WATCHED: Final = 12
BIG_COPY_BYTES: Final = 32 * 1024 * 1024

_List = builtins.list


def qt_trash(path: Path) -> bool:
    """Move a path to the Recycle Bin (``QFile.moveToTrash``)."""
    result = QFile.moveToTrash(str(path))
    return bool(result[0] if isinstance(result, tuple) else result)


def _url_to_path(item: object) -> str:
    if isinstance(item, QUrl):
        return item.toLocalFile()
    text = str(item)
    return QUrl(text).toLocalFile() if text.lower().startswith("file:") else text


def _is_big(paths: _List[str]) -> bool:
    """Do these files and folders add up to more than a quick copy?"""
    total = 0
    for text in paths:
        path = Path(text)
        try:
            if path.is_file():
                total += path.stat().st_size
            elif path.is_dir():
                for current, _folders, files in os.walk(path):
                    for name in files:
                        total += (Path(current) / name).stat().st_size
                        if total > BIG_COPY_BYTES:
                            return True
        except OSError:
            continue
        if total > BIG_COPY_BYTES:
            return True
    return False


class Library(QObject):
    """The app's library folder, exposed to QML as ``Library``."""

    changed = Signal()
    rootChanged = Signal()
    moveFinished = Signal(str)        # "" or why the library could not move
    asyncError = Signal(str)          # a big copy that failed after the call returned
    _jobDone = Signal(object)

    def __init__(
        self,
        library: FileLibrary,
        *,
        stub_launch: bool = False,
        on_root_changed: Callable[[str], None] | None = None,
        sync_jobs: bool = False,
    ) -> None:
        super().__init__()
        self._library = library
        # Under test, open() and showInExplorer() only record the call (no real app starts).
        self._stub_launch = stub_launch
        self.launched: _List[tuple[str, str]] = []
        self._on_root_changed = on_root_changed
        self.sync_jobs = sync_jobs
        self._watched: _List[str] = []
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_fs_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self.changed.emit)
        self._jobDone.connect(self._finish_job)
        self._busy = False

    @property
    def library(self) -> FileLibrary:
        return self._library

    @property
    def root(self) -> Path:
        return self._library.root

    def _root_path(self) -> str:
        return str(self._library.root)

    rootPath = Property(str, _root_path, notify=rootChanged)  # noqa: N815

    def close(self) -> None:
        """Stop watching folders (so a temp library can be deleted on Windows)."""
        directories = self._watcher.directories()
        if directories:
            self._watcher.removePaths(directories)
        self._watched.clear()

    def _watch(self, folder: Path) -> None:
        text = str(folder)
        if text in self._watched:
            self._watched.remove(text)
        else:
            self._watcher.addPath(text)
        self._watched.append(text)
        while len(self._watched) > _MAX_WATCHED:
            self._watcher.removePath(self._watched.pop(0))

    def _on_fs_changed(self, _path: str) -> None:
        self._debounce.start()

    def _finish(self, error: str) -> str:
        self.changed.emit()
        return error

    def _run_job(self, work: Callable[[], Any], done: Callable[[Any, Exception | None], None]) -> None:
        if self.sync_jobs:
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            done(result, error)
            return

        def target() -> None:
            payload: tuple[Any, ...]
            try:
                payload = (done, work(), None)
            except Exception as exc:
                payload = (done, None, exc)
            self._jobDone.emit(payload)

        threading.Thread(target=target, name="library-job", daemon=True).start()

    @Slot(object)
    def _finish_job(self, payload: Any) -> None:
        done, result, error = payload
        done(result, error)

    # ── Reading ──────────────────────────────────────────────────────
    @Slot(str, result="QVariantList")
    def list(self, rel: str) -> _List[dict[str, Any]]:
        try:
            self._watch(self._library.resolve(rel))
        except (ValueError, OSError):
            return []
        return _List(self._library.list_dir(rel))  # type: ignore[arg-type]

    @Slot(str, result="QVariantList")
    def search(self, query: str) -> _List[dict[str, Any]]:
        return _List(self._library.search(query))  # type: ignore[arg-type]

    @Slot(str, result=bool)
    def isDir(self, rel: str) -> bool:  # noqa: N802
        return self._library.is_dir(rel)

    @Slot(result="QVariantList")
    def folders(self) -> _List[str]:
        return self._library.folders()

    @Slot(str, result=str)
    def uniqueFolderName(self, parent_rel: str) -> str:  # noqa: N802
        return self._library.unique_folder_name(parent_rel)

    @Slot(result=str)
    def usage(self) -> str:
        return self._library.usage()

    # ── Writing ──────────────────────────────────────────────────────
    @Slot("QVariantList", str, result=str)
    def addFiles(self, urls: Sequence[object], dest_rel: str) -> str:  # noqa: N802
        """Copy files in.  A big copy runs on a worker thread: this returns "" at once and a
        failure comes later as ``asyncError`` (``changed`` follows either way)."""
        sources = [text for text in (_url_to_path(item) for item in urls) if text]
        if not _is_big(sources):
            return self._finish(self._library.add_files(sources, dest_rel))

        def done(result: Any, error: Exception | None) -> None:
            message = f"Couldn't copy: {error}" if error is not None else str(result)
            if message:
                self.asyncError.emit(message)
            self.changed.emit()

        self._run_job(lambda: self._library.add_files(sources, dest_rel), done)
        return ""

    @Slot(str, result=str)
    def pasteInto(self, dest_rel: str) -> str:  # noqa: N802
        mime = QGuiApplication.clipboard().mimeData()
        urls = [u for u in (mime.urls() if mime is not None else []) if u.isLocalFile()]
        if not urls:
            return "Clipboard has no files"
        return self.addFiles(urls, dest_rel)

    @Slot(str, str, result=str)
    def createFolder(self, parent_rel: str, name: str) -> str:  # noqa: N802
        return self._finish(self._library.create_folder(parent_rel, name))

    @Slot(str, str, result=str)
    def rename(self, rel: str, new_name: str) -> str:
        return self._finish(self._library.rename(rel, new_name))

    @Slot(str, result=str)
    def remove(self, rel: str) -> str:
        return self._finish(self._library.remove(rel))

    @Slot(str, str, result=str)
    def move(self, rel: str, dest_rel: str) -> str:
        return self._finish(self._library.move(rel, dest_rel))

    # ── The library location (Settings) ──────────────────────────────
    @Slot(str, result=str)
    def checkNewRoot(self, path: str) -> str:  # noqa: N802
        """"" when ``path`` is an empty or new folder the library may move to, else why not."""
        return self._library.check_new_root(_url_to_path(path))

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        if self._stub_launch:
            self.launched.append(("openFolder", ""))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))

    @Slot(str, result=str)
    def moveTo(self, path: str) -> str:  # noqa: N802
        """Move the library to an empty or new folder.  The move runs on a worker thread:
        this returns "" at once (or why the folder is not allowed) and ``moveFinished`` says how
        it went."""
        text = _url_to_path(path).strip()
        error = self._library.check_new_root(text)
        if error:
            return error
        if self._busy:
            return "A move is already running"
        self._busy = True
        self.close()      # a watched folder can't be moved on Windows

        def done(result: Any, failure: Exception | None) -> None:
            self._busy = False
            message = f"Couldn't move the files: {failure}" if failure is not None else str(result)
            if not message:
                self.rootChanged.emit()
                if self._on_root_changed is not None:
                    self._on_root_changed(str(self.root))
            self.changed.emit()
            self.moveFinished.emit(message)

        self._run_job(lambda: self._library.move_to(text), done)
        return ""

    # ── Shell integration ────────────────────────────────────────────
    @Slot(str)
    def open(self, rel: str) -> None:
        try:
            path = self._library.resolve(rel)
        except ValueError:
            return
        if self._stub_launch:
            self.launched.append(("open", rel))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot(str)
    def showInExplorer(self, rel: str) -> None:  # noqa: N802
        try:
            path = self._library.resolve(rel)
        except ValueError:
            return
        if self._stub_launch:
            self.launched.append(("explorer", rel))
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603, S607
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    @Slot(str, result=str)
    def copyToClipboard(self, rel: str) -> str:  # noqa: N802
        try:
            path = self._library.resolve(rel)
        except ValueError as error:
            return str(error)
        if not path.exists():
            return "Nothing to copy"
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        # Explorer's paste treats a file list as "move" unless told it's a copy.
        mime.setData("Preferred DropEffect", QByteArray(struct.pack("<I", _DROP_EFFECT_COPY)))
        QGuiApplication.clipboard().setMimeData(mime)
        return ""
