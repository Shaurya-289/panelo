"""The file library: one master folder on disk, managed without a database.

Every path that crosses the API is relative to the library root and uses ``/``; anything that
would resolve outside the root is rejected.  Methods that change the disk return an error
message, or ``""`` on success, so a caller can show it as is.

This is the pure-Python half of ``prototype/files.py``.  The Qt adapter (change signals, the
file-system watcher, clipboard, ``QDesktopServices``) lives with the UI, and so does moving a
file to the Recycle Bin: it is handed in as the ``trash`` callable (``QFile.moveToTrash``).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final, TypedDict

from platformdirs import user_data_dir

APP_NAME: Final = "Panelo"
APP_AUTHOR: Final = "Panelo"
LIBRARY_FOLDER_NAME: Final = "Files"

KIND_BY_EXT: Final[dict[str, set[str]]] = {
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
}  # fmt: skip
_KIND_OF: Final = {ext: kind for kind, exts in KIND_BY_EXT.items() for ext in exts}
_INVALID_NAME_CHARS: Final = frozenset('\\/:*?"<>|')
_HIDDEN_NAMES: Final = frozenset({"desktop.ini", "thumbs.db"})
_MAX_SEARCH_RESULTS: Final = 200


class FileEntry(TypedDict):
    name: str
    rel: str
    isDir: bool
    ext: str
    kind: str
    size: str
    modified: str
    items: int
    url: str


class SearchEntry(FileEntry):
    parent: str


def default_library_root() -> Path:
    """The platform default: ``Files`` inside the application's per-user data folder."""
    return Path(user_data_dir(APP_NAME, APP_AUTHOR)) / LIBRARY_FOLDER_NAME


def library_root(library_path: str) -> Path:
    """The root for the ``library_path`` setting (empty means the platform default)."""
    text = library_path.strip()
    return Path(text) if text else default_library_root()


def kind_of(name: str) -> str:
    return _KIND_OF.get(Path(name).suffix.lstrip(".").lower(), "other")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # unreachable: the GB step always returns


def human_time(stamp: float, now: datetime | None = None) -> str:
    """``Today 14:05``, ``Oct 28`` or ``Oct 28 2024`` (the year only when it is not this one)."""
    moment = datetime.fromtimestamp(stamp)
    current = now or datetime.now()
    if moment.date() == current.date():
        return f"Today {moment:%H:%M}"
    text = f"{moment:%b} {moment.day}"
    return text if moment.year == current.year else f"{text} {moment.year}"


def valid_name(name: str) -> str:
    """An error message, or ``""`` when ``name`` is a usable file or folder name."""
    clean = name.strip()
    if not clean or clean in (".", ".."):
        return "Name can't be empty"
    if any(ch in _INVALID_NAME_CHARS for ch in clean):
        return 'Name can\'t contain \\ / : * ? " < > |'
    if clean.endswith("."):
        return "Name can't end with a dot"
    return ""


def unique_path(folder: Path, name: str) -> Path:
    """``folder/name``, or ``folder/name (2).ext``, ``(3)`` ... when that is taken."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, suffix = (name, "") if candidate.is_dir() else (Path(name).stem, Path(name).suffix)
    count = 2
    while True:
        candidate = folder / f"{stem} ({count}){suffix}"
        if not candidate.exists():
            return candidate
        count += 1


def _hidden(name: str) -> bool:
    return name.startswith(".") or name.lower() in _HIDDEN_NAMES


class FileLibrary:
    """The library folder.  ``trash`` moves a path to the Recycle Bin and says whether it did."""

    def __init__(self, root: Path, *, trash: Callable[[Path], bool]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()
        self._trash = trash

    @property
    def root(self) -> Path:
        return self._root

    # -- Paths ------------------------------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        """The absolute path of ``rel``; ``ValueError`` when it would leave the library."""
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

    def _entry(self, path: Path) -> FileEntry:
        is_dir = path.is_dir()
        try:
            info = path.stat()
            modified, size = human_time(info.st_mtime), info.st_size
        except OSError:
            modified, size = "", 0
        items = 0
        if is_dir:
            try:
                items = sum(1 for child in path.iterdir() if not _hidden(child.name))
            except OSError:
                items = 0
        return {
            "name": path.name,
            "rel": self._rel(path),
            "isDir": is_dir,
            "ext": "" if is_dir else path.suffix.lstrip(".").lower(),
            "kind": "folder" if is_dir else kind_of(path.name),
            "size": "" if is_dir else human_size(size),
            "modified": modified,
            "items": items,
            "url": path.as_uri(),
        }

    # -- Reading ----------------------------------------------------------------------------
    def list_dir(self, rel: str) -> list[FileEntry]:
        """The visible children of a folder, folders first, then by name."""
        try:
            folder = self.resolve(rel)
            children = [c for c in folder.iterdir() if not _hidden(c.name)]
        except (ValueError, OSError):
            return []
        children.sort(key=lambda c: (not c.is_dir(), c.name.lower()))
        return [self._entry(child) for child in children]

    def search(self, query: str) -> list[SearchEntry]:
        """Files and folders whose name contains ``query`` (any case), at most 200."""
        needle = query.strip().lower()
        if not needle:
            return []
        found: list[Path] = []
        for current, folders, files in os.walk(self._root):
            folders[:] = [f for f in folders if not _hidden(f)]
            found.extend(
                Path(current, name)
                for name in (*folders, *files)
                if needle in name.lower() and not _hidden(name)
            )
            if len(found) >= _MAX_SEARCH_RESULTS:
                break
        found.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        return [
            SearchEntry(**self._entry(path), parent=self._rel(path.parent))
            for path in found[:_MAX_SEARCH_RESULTS]
        ]

    def is_dir(self, rel: str) -> bool:
        try:
            return self.resolve(rel).is_dir()
        except ValueError:
            return False

    def folders(self) -> list[str]:
        """Every folder below the root, as relative paths sorted by their parts."""
        found: list[str] = []
        for current, names, _files in os.walk(self._root):
            names[:] = sorted((n for n in names if not _hidden(n)), key=str.lower)
            found.extend(self._rel(Path(current, n)) for n in names)
        found.sort(key=lambda r: [part.lower() for part in r.split("/")])
        return found

    def unique_folder_name(self, parent_rel: str) -> str:
        try:
            return unique_path(self.resolve(parent_rel), "New folder").name
        except ValueError:
            return "New folder"

    def usage(self) -> str:
        """Storage used by the library, e.g. ``142.0 MB, 37 files``."""
        total, count = 0, 0
        for current, folders, files in os.walk(self._root):
            folders[:] = [f for f in folders if not _hidden(f)]
            for name in files:
                if _hidden(name):
                    continue
                try:
                    total += Path(current, name).stat().st_size
                except OSError:
                    continue
                count += 1
        return f"{human_size(total)}, {count} file{'' if count == 1 else 's'}"

    # -- Writing ----------------------------------------------------------------------------
    @staticmethod
    def _copy_in(source: Path, dest_dir: Path) -> str:
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

    def add_files(self, sources: Sequence[str | Path], dest_rel: str) -> str:
        """Copy local files or folders into a library folder (the originals stay)."""
        try:
            dest = self.resolve(dest_rel)
        except ValueError as error:
            return str(error)
        if not dest.is_dir():
            return "Destination folder doesn't exist"
        errors: list[str] = []
        count = 0
        for item in sources:
            local = str(item)
            if not local:
                continue
            count += 1
            message = self._copy_in(Path(local), dest)
            if message:
                errors.append(message)
        if count == 0:
            return "Nothing to add"
        return "; ".join(errors)

    def create_folder(self, parent_rel: str, name: str) -> str:
        try:
            parent = self.resolve(parent_rel)
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
        return ""

    def rename(self, rel: str, new_name: str) -> str:
        try:
            path = self.resolve(rel)
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
        return ""

    def remove(self, rel: str) -> str:
        """Move a file or folder to the Recycle Bin (the injected ``trash``)."""
        try:
            path = self.resolve(rel)
        except ValueError as error:
            return str(error)
        if path == self._root or not path.exists():
            return "Nothing to delete"
        try:
            if not self._trash(path):
                return "Couldn't move it to the Recycle Bin"
        except OSError as error:
            return f"Couldn't delete: {error.strerror or error}"
        return ""

    def move(self, rel: str, dest_rel: str) -> str:
        try:
            path = self.resolve(rel)
            dest = self.resolve(dest_rel)
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
        return ""

    # -- The library location (Settings) ---------------------------------------------------
    def check_new_root(self, path: str) -> str:
        """``""`` when ``path`` is an empty or new folder the library may move to, else why not."""
        text = path.strip()
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

    def move_to(self, path: str) -> str:
        """Move every top-level entry into an empty or new folder, then use it as the root.

        A file-system watcher on the old root must be released first (a watched folder cannot
        be moved on Windows).
        """
        error = self.check_new_root(path)
        if error:
            return error
        target = Path(path.strip()).resolve()
        try:
            target.mkdir(parents=True, exist_ok=True)
            for entry in list(self._root.iterdir()):
                shutil.move(str(entry), str(target / entry.name))
        except OSError as problem:
            return f"Couldn't move the files: {problem.strerror or problem}"
        self._root = target
        return ""
