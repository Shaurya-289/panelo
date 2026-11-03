"""File library backend for the prototype's Files explorer.

One master folder ("Files") on disk. Every path that crosses into QML is relative to that
root and uses "/"; anything that would resolve outside the root is rejected.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath

from PySide6.QtCore import (
    Property,
    QByteArray,
    QFile,
    QFileSystemWatcher,
    QMimeData,
    QObject,
    QPointF,
    QRectF,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPageSize,
    QPainter,
    QPainterPath,
    QPdfWriter,
    QPen,
)

KIND_BY_EXT: dict[str, set[str]] = {
    "pdf": {"pdf"},
    "slides": {"ppt", "pptx", "key", "odp"},
    "doc": {"doc", "docx", "odt", "rtf", "md", "txt"},
    "sheet": {"xls", "xlsx", "csv", "ods"},
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"},
    "video": {"mp4", "mkv", "mov", "avi", "webm", "wmv", "m4v"},
    "audio": {"mp3", "wav", "flac", "ogg", "m4a", "aac"},
    "archive": {"zip", "rar", "7z", "tar", "gz"},
    "code": {
        "py", "js", "ts", "tsx", "jsx", "html", "css", "json", "qml", "c", "cpp", "h", "cs",
        "java", "go", "rs", "sh", "yml", "yaml", "toml", "xml", "sql",
    },
}
_KIND_OF = {ext: kind for kind, exts in KIND_BY_EXT.items() for ext in exts}
_INVALID_NAME_CHARS = set('\\/:*?"<>|')
_HIDDEN_NAMES = {"desktop.ini", "thumbs.db"}
_DROP_EFFECT_COPY = 1
_MAX_WATCHED = 12
_MAX_SEARCH_RESULTS = 200


def kind_of(name: str) -> str:
    return _KIND_OF.get(Path(name).suffix.lstrip(".").lower(), "other")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def human_time(stamp: float) -> str:
    moment = datetime.fromtimestamp(stamp)
    now = datetime.now()
    if moment.date() == now.date():
        return f"Today {moment:%H:%M}"
    text = f"{moment:%b} {moment.day}"
    return text if moment.year == now.year else f"{text} {moment.year}"


def valid_name(name: str) -> str:
    """Return an error message, or "" when `name` is a usable file or folder name."""
    clean = name.strip()
    if not clean or clean in (".", ".."):
        return "Name can't be empty"
    if any(ch in _INVALID_NAME_CHARS for ch in clean):
        return 'Name can\'t contain \\ / : * ? " < > |'
    if clean.endswith("."):
        return "Name can't end with a dot"
    return ""


def unique_path(folder: Path, name: str) -> Path:
    """`folder/name`, or `folder/name (2).ext`, `(3)`… when taken."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, suffix = (name, "") if (folder / name).is_dir() else (Path(name).stem, Path(name).suffix)
    count = 2
    while True:
        candidate = folder / f"{stem} ({count}){suffix}"
        if not candidate.exists():
            return candidate
        count += 1


def _url_to_path(item: object) -> str:
    if isinstance(item, QUrl):
        return item.toLocalFile()
    text = str(item)
    return QUrl(text).toLocalFile() if text.lower().startswith("file:") else text


class FileLibrary(QObject):
    """The app's own library folder, exposed to QML as `Library`."""

    changed = Signal()
    rootChanged = Signal()

    def __init__(
        self, root: Path, *, hard_delete: bool = False, seed: bool = True, stub_launch: bool = False
    ) -> None:
        super().__init__()
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()
        self._hard_delete = hard_delete
        # Under the self-test, open() and showInExplorer() only record the call (no real app starts).
        self._stub_launch = stub_launch
        self.launched: list[tuple[str, str]] = []
        self._watched: list[str] = []
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_fs_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self.changed.emit)
        if seed and not any(self._root.iterdir()):
            self._seed()

    @property
    def root(self) -> Path:
        return self._root

    def _root_path(self) -> str:
        return str(self._root)

    rootPath = Property(str, _root_path, notify=rootChanged)  # noqa: N815

    def close(self) -> None:
        """Stop watching folders (so a temp library can be deleted on Windows)."""
        directories = self._watcher.directories()
        if directories:
            self._watcher.removePaths(directories)
        self._watched.clear()

    # ── Paths ────────────────────────────────────────────────────────
    def _resolve(self, rel: str) -> Path:
        clean = rel.replace("\\", "/").strip("/")
        if PurePosixPath(clean).is_absolute() or ":" in clean:
            raise ValueError("Path is outside the library")
        target = (self._root / clean).resolve() if clean else self._root
        if target != self._root and not target.is_relative_to(self._root):
            raise ValueError("Path is outside the library")
        return target

    def _rel(self, path: Path) -> str:
        relative = path.relative_to(self._root)
        return "" if str(relative) == "." else relative.as_posix()

    def _entry(self, path: Path) -> dict[str, object]:
        is_dir = path.is_dir()
        try:
            info = path.stat()
            modified, size = human_time(info.st_mtime), info.st_size
        except OSError:
            modified, size = "", 0
        ext = "" if is_dir else path.suffix.lstrip(".").lower()
        items = 0
        if is_dir:
            try:
                items = sum(1 for child in path.iterdir() if not self._hidden(child.name))
            except OSError:
                items = 0
        return {
            "name": path.name,
            "rel": self._rel(path),
            "isDir": is_dir,
            "ext": ext,
            "kind": "folder" if is_dir else kind_of(path.name),
            "size": "" if is_dir else human_size(size),
            "modified": modified,
            "items": items,
            "url": QUrl.fromLocalFile(str(path)).toString(),
        }

    @staticmethod
    def _hidden(name: str) -> bool:
        return name.startswith(".") or name.lower() in _HIDDEN_NAMES

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

    # ── Reading ──────────────────────────────────────────────────────
    @Slot(str, result="QVariantList")
    def list(self, rel: str) -> list[dict[str, object]]:
        try:
            folder = self._resolve(rel)
            children = [c for c in folder.iterdir() if not self._hidden(c.name)]
        except (ValueError, OSError):
            return []
        self._watch(folder)
        children.sort(key=lambda c: (not c.is_dir(), c.name.lower()))
        return [self._entry(child) for child in children]

    @Slot(str, result="QVariantList")
    def search(self, query: str) -> list[dict[str, object]]:
        needle = query.strip().lower()
        if not needle:
            return []
        found: list[Path] = []
        for current, folders, files in os.walk(self._root):
            folders[:] = [f for f in folders if not self._hidden(f)]
            found.extend(
                Path(current, name)
                for name in (*folders, *files)
                if needle in name.lower() and not self._hidden(name)
            )
            if len(found) >= _MAX_SEARCH_RESULTS:
                break
        found.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        results = []
        for path in found[:_MAX_SEARCH_RESULTS]:
            entry = self._entry(path)
            entry["parent"] = self._rel(path.parent)
            results.append(entry)
        return results

    @Slot(str, result=bool)
    def isDir(self, rel: str) -> bool:  # noqa: N802
        try:
            return self._resolve(rel).is_dir()
        except ValueError:
            return False

    @Slot(result="QVariantList")
    def folders(self) -> list[str]:
        out: list[str] = []
        for current, names, _files in os.walk(self._root):
            names[:] = sorted((n for n in names if not self._hidden(n)), key=str.lower)
            out.extend(self._rel(Path(current, n)) for n in names)
        out.sort(key=lambda r: [part.lower() for part in r.split("/")])
        return out

    @Slot(str, result=str)
    def uniqueFolderName(self, parent_rel: str) -> str:  # noqa: N802
        try:
            return unique_path(self._resolve(parent_rel), "New folder").name
        except ValueError:
            return "New folder"

    # ── Writing ──────────────────────────────────────────────────────
    def _finish(self, errors: list[str]) -> str:
        self.changed.emit()
        return "; ".join(errors)

    def _copy_in(self, source: Path, dest_dir: Path) -> str:
        if not source.exists():
            return f"{source.name} no longer exists"
        if source.is_dir() and (dest_dir == source or dest_dir.is_relative_to(source)):
            return f"Can't copy {source.name} into itself"
        target = unique_path(dest_dir, source.name)
        try:
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        except OSError as error:
            return f"Couldn't copy {source.name}: {error.strerror or error}"
        return ""

    @Slot("QVariantList", str, result=str)
    def addFiles(self, urls: list[object], dest_rel: str) -> str:  # noqa: N802
        try:
            dest = self._resolve(dest_rel)
        except ValueError as error:
            return str(error)
        if not dest.is_dir():
            return "Destination folder doesn't exist"
        errors = []
        count = 0
        for item in urls:
            local = _url_to_path(item)
            if not local:
                continue
            count += 1
            message = self._copy_in(Path(local), dest)
            if message:
                errors.append(message)
        if count == 0:
            return "Nothing to add"
        return self._finish(errors)

    @Slot(str, result=str)
    def pasteInto(self, dest_rel: str) -> str:  # noqa: N802
        mime = QGuiApplication.clipboard().mimeData()
        urls = [u for u in (mime.urls() if mime is not None else []) if u.isLocalFile()]
        if not urls:
            return "Clipboard has no files"
        return self.addFiles(urls, dest_rel)

    @Slot(str, str, result=str)
    def createFolder(self, parent_rel: str, name: str) -> str:  # noqa: N802
        try:
            parent = self._resolve(parent_rel)
        except ValueError as error:
            return str(error)
        clean = name.strip() or "New folder"
        problem = valid_name(clean)
        if problem:
            return problem
        try:
            unique_path(parent, clean).mkdir()
        except OSError as error:
            return f"Couldn't create the folder: {error.strerror or error}"
        return self._finish([])

    @Slot(str, str, result=str)
    def rename(self, rel: str, new_name: str) -> str:
        try:
            path = self._resolve(rel)
        except ValueError as error:
            return str(error)
        if path == self._root:
            return "Can't rename the library"
        clean = new_name.strip()
        problem = valid_name(clean)
        if problem:
            return problem
        target = path.with_name(clean)
        if clean == path.name:
            return ""
        if target.exists() and target.name.lower() != path.name.lower():
            return f"“{clean}” already exists here"
        try:
            path.rename(target)
        except OSError as error:
            return f"Couldn't rename: {error.strerror or error}"
        return self._finish([])

    @Slot(str, result=str)
    def remove(self, rel: str) -> str:
        try:
            path = self._resolve(rel)
        except ValueError as error:
            return str(error)
        if path == self._root or not path.exists():
            return "Nothing to delete"
        try:
            if self._hard_delete:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            else:
                result = QFile.moveToTrash(str(path))
                moved = result[0] if isinstance(result, tuple) else result
                if not moved:
                    return "Couldn't move it to the Recycle Bin"
        except OSError as error:
            return f"Couldn't delete: {error.strerror or error}"
        return self._finish([])

    @Slot(str, str, result=str)
    def move(self, rel: str, dest_rel: str) -> str:
        try:
            path = self._resolve(rel)
            dest = self._resolve(dest_rel)
        except ValueError as error:
            return str(error)
        if path == self._root or not path.exists():
            return "Nothing to move"
        if not dest.is_dir():
            return "Destination folder doesn't exist"
        if path.is_dir() and (dest == path or dest.is_relative_to(path)):
            return "Can't move a folder into itself"
        if dest == path.parent:
            return ""
        target = unique_path(dest, path.name)
        try:
            shutil.move(str(path), str(target))
        except OSError as error:
            return f"Couldn't move: {error.strerror or error}"
        return self._finish([])

    # ── The library location (Settings) ──────────────────────────────
    @Slot(result=str)
    def usage(self) -> str:
        """Storage used by the library, e.g. "142 MB, 37 files"."""
        total, count = 0, 0
        for current, folders, files in os.walk(self._root):
            folders[:] = [f for f in folders if not self._hidden(f)]
            for name in files:
                if self._hidden(name):
                    continue
                try:
                    total += Path(current, name).stat().st_size
                except OSError:
                    continue
                count += 1
        return f"{human_size(total)}, {count} file{'' if count == 1 else 's'}"

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        if self._stub_launch:
            self.launched.append(("openFolder", ""))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root)))

    @Slot(str, result=str)
    def checkNewRoot(self, path: str) -> str:  # noqa: N802
        """"" when `path` is an empty or new folder the library may move to, else why not."""
        text = _url_to_path(path).strip()
        if not text:
            return "Choose a folder"
        try:
            target = Path(text).resolve()
        except OSError:
            return "That folder can't be used"
        if target == self._root:
            return "That is already the library folder"
        if target.is_relative_to(self._root) or self._root.is_relative_to(target):
            return "Choose a folder outside the current library"
        if target.exists():
            if not target.is_dir():
                return "That is a file, not a folder"
            try:
                if any(target.iterdir()):
                    return "Choose an empty or new folder: that one has files in it"
            except OSError:
                return "That folder can't be read"
        return ""

    @Slot(str, result=str)
    def moveTo(self, path: str) -> str:  # noqa: N802
        """Move every top-level entry of the library into an empty or new folder, then use that
        folder as the library root."""
        error = self.checkNewRoot(path)
        if error:
            return error
        target = Path(_url_to_path(path).strip()).resolve()
        self.close()  # a watched folder can't be moved on Windows
        try:
            target.mkdir(parents=True, exist_ok=True)
            for entry in list(self._root.iterdir()):
                shutil.move(str(entry), str(target / entry.name))
        except OSError as problem:
            return f"Couldn't move the files: {problem.strerror or problem}"
        self._root = target
        self.rootChanged.emit()
        self.changed.emit()
        return ""

    # ── Shell integration ────────────────────────────────────────────
    @Slot(str)
    def open(self, rel: str) -> None:
        with contextlib.suppress(ValueError):
            path = self._resolve(rel)
            if self._stub_launch:
                self.launched.append(("open", rel))
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot(str)
    def showInExplorer(self, rel: str) -> None:  # noqa: N802
        try:
            path = self._resolve(rel)
        except ValueError:
            return
        if self._stub_launch:
            self.launched.append(("explorer", rel))
            return
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')  # noqa: S603
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    @Slot(str, result=str)
    def copyToClipboard(self, rel: str) -> str:  # noqa: N802
        try:
            path = self._resolve(rel)
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

    # ── Seed data ────────────────────────────────────────────────────
    def _seed(self) -> None:
        for folder in ("Study/Physics", "Study/Maths", "Study/Chemistry", "Coding", "Personal"):
            (self._root / folder).mkdir(parents=True, exist_ok=True)
        write_seed_pdf(self._root / "Study/Physics/Chapter 3 - Kinematics.pdf")
        write_seed_png(self._root / "Study/Physics/Lab graph.png")
        (self._root / "Coding/ideas.txt").write_text(
            "Ideas\n- A glass UI that feels calm\n- Quick overlay on a hotkey\n- Keep files next to notes\n",
            encoding="utf-8",
        )
        (self._root / "Personal/Trip budget.csv").write_text(
            "Item,Cost\nFlights,420\nHotel,610\nFood,240\nLocal transport,90\n", encoding="utf-8"
        )
        (self._root / "Study/Maths/Formulas.md").write_text(
            "# Formulas\n\n- Quadratic: x = (-b ± sqrt(b^2 - 4ac)) / 2a\n- Area of a circle: pi r^2\n",
            encoding="utf-8",
        )


def write_seed_pdf(path: Path) -> None:
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(72)
    writer.setTitle("Chapter 3 - Kinematics")
    painter = QPainter(writer)
    title = QFont("Segoe UI")
    title.setPixelSize(26)
    painter.setFont(title)
    painter.drawText(60, 100, "Chapter 3 - Kinematics")
    body = QFont("Segoe UI")
    body.setPixelSize(13)
    painter.setFont(body)
    for index, line in enumerate(
        (
            "Motion in a straight line, with constant acceleration.",
            "v = u + at",
            "s = ut + 1/2 at^2",
            "v^2 = u^2 + 2as",
            "Sketch the velocity-time graph before solving a problem.",
        )
    ):
        painter.drawText(60, 150 + index * 24, line)
    painter.end()


def write_seed_png(path: Path) -> None:
    image = QImage(480, 320, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 480, 320)
    gradient.setColorAt(0.0, QColor("#1d2b4a"))
    gradient.setColorAt(1.0, QColor("#2a1d3d"))
    painter.fillRect(image.rect(), gradient)
    painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
    for x in range(40, 460, 40):
        painter.drawLine(x, 30, x, 290)
    for y in range(30, 300, 40):
        painter.drawLine(40, y, 440, y)
    painter.setPen(QPen(QColor("#ffffff"), 2))
    painter.drawLine(40, 290, 440, 290)
    painter.drawLine(40, 30, 40, 290)
    curve = QPainterPath(QPointF(40, 270))
    curve.cubicTo(QPointF(140, 260), QPointF(220, 120), QPointF(440, 50))
    painter.setPen(QPen(QColor("#ffb454"), 3))
    painter.drawPath(curve)
    painter.setBrush(QColor("#ffb454"))
    for x, y in ((40, 270), (150, 235), (260, 150), (350, 85), (440, 50)):
        painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
    painter.end()
    image.save(str(path))
