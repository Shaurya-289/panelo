"""The Files library as QML sees it: the Qt adapter over the pure ``FileLibrary``.

Every file operation happens in a pytest temp folder; the Recycle Bin is a fake that records
what it was asked to remove; no real application is launched."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from panelo.files.library import FileLibrary
from panelo.ui.bridge import library as library_module
from panelo.ui.bridge.library import Library


class BinSpy:
    """A Recycle Bin that only remembers (and deletes, so the folder looks emptied)."""

    def __init__(self) -> None:
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> bool:
        self.paths.append(path)
        path.unlink()
        return True


@pytest.fixture
def bin_spy() -> BinSpy:
    return BinSpy()


@pytest.fixture
def library(qapp: QApplication, tmp_path: Path, bin_spy: BinSpy) -> Library:
    root = tmp_path / "library"
    (root / "Study").mkdir(parents=True)
    (root / "Study" / "notes.txt").write_text("hello", encoding="utf-8")
    bridge = Library(FileLibrary(root, trash=bin_spy), stub_launch=True, sync_jobs=True)
    yield bridge
    bridge.close()


def names(library: Library, rel: str = "") -> list[str]:
    return [entry["name"] for entry in library.list(rel)]


def test_list_gives_qml_dictionaries(library: Library) -> None:
    entry = library.list("Study")[0]
    assert entry["name"] == "notes.txt" and entry["kind"] == "doc" and entry["size"] == "5 B"
    assert entry["rel"] == "Study/notes.txt" and entry["url"].startswith("file:///")
    assert names(library) == ["Study"]


def test_paths_that_leave_the_library_are_refused(library: Library, tmp_path: Path) -> None:
    assert library.list("..") == [] and library.list("C:/Windows") == []
    assert library.rename("..", "x") != ""
    assert library.move("Study/notes.txt", "..") != ""
    assert library.remove("../library2") != ""


def test_create_rename_move_and_the_clash_name(library: Library, tmp_path: Path) -> None:
    changes: list[int] = []
    library.changed.connect(lambda: changes.append(1))
    assert library.createFolder("", "Coding") == ""
    assert library.createFolder("", "Coding") == ""  # a clash gets " (2)"
    assert names(library) == ["Coding", "Coding (2)", "Study"]
    assert library.rename("Coding (2)", "Personal") == ""
    assert library.rename("Personal", "a/b") != "" and library.rename("Personal", " ") != ""
    assert library.move("Study/notes.txt", "Personal") == ""
    assert names(library, "Personal") == ["notes.txt"]
    assert library.move("Study", "Study") != ""
    assert len(changes) >= 4


def test_remove_goes_through_the_recycle_bin(library: Library, bin_spy: BinSpy) -> None:
    assert library.remove("Study/notes.txt") == ""
    assert [p.name for p in bin_spy.paths] == ["notes.txt"]
    assert library.remove("Study/notes.txt") == "Nothing to delete"


def test_add_files_copies_and_leaves_the_original(library: Library, tmp_path: Path) -> None:
    source = tmp_path / "outside.txt"
    source.write_text("data", encoding="utf-8")
    url = QUrl.fromLocalFile(str(source)).toString()
    assert library.addFiles([url], "Study") == ""
    assert library.addFiles([url], "Study") == ""
    assert names(library, "Study") == ["notes.txt", "outside (2).txt", "outside.txt"]
    assert source.exists()
    assert library.addFiles([], "Study") == "Nothing to add"
    assert library.addFiles([url], "Missing") != ""


def test_a_big_copy_runs_on_a_worker_and_reports_later(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library_module, "BIG_COPY_BYTES", 3)
    errors: list[str] = []
    library.asyncError.connect(errors.append)
    source = tmp_path / "big.bin"
    source.write_bytes(b"0123456789")
    assert library.addFiles([str(source)], "Study") == ""  # returns at once
    assert "big.bin" in names(library, "Study") and errors == []
    monkeypatch.setattr(library_module, "_is_big", lambda _paths: True)
    assert library.addFiles([str(tmp_path / "ghost.bin")], "Study") == ""
    assert errors == ["ghost.bin no longer exists"]


def test_search_folders_usage_and_unique_name(library: Library) -> None:
    assert [e["name"] for e in library.search("NOTES")] == ["notes.txt"]
    assert library.folders() == ["Study"]
    assert library.usage() == "5 B, 1 file"
    assert library.uniqueFolderName("") == "New folder"
    library.createFolder("", "New folder")
    assert library.uniqueFolderName("") == "New folder (2)"


def test_moving_the_library_checks_then_moves(library: Library, tmp_path: Path) -> None:
    moved: list[str] = []
    finished: list[str] = []
    library._on_root_changed = moved.append
    library.moveFinished.connect(finished.append)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("x", encoding="utf-8")
    assert "empty or new" in library.moveTo(str(busy))
    assert library.checkNewRoot(str(library.root)) == "That is already the library folder"
    assert library.checkNewRoot(str(library.root / "Study")) != ""
    target = tmp_path / "elsewhere"
    assert library.moveTo(QUrl.fromLocalFile(str(target)).toString()) == ""
    assert finished == [""] and moved == [str(target.resolve())]
    assert library.root == target.resolve() and library.rootPath == str(target.resolve())
    assert names(library, "Study") == ["notes.txt"]


def test_open_and_show_in_explorer_are_recorded_not_launched(library: Library) -> None:
    library.open("Study/notes.txt")
    library.showInExplorer("Study/notes.txt")
    library.openFolder()
    library.open("../outside")
    assert library.launched == [
        ("open", "Study/notes.txt"), ("explorer", "Study/notes.txt"), ("openFolder", "")
    ]


def test_copy_to_clipboard_puts_a_file_url_with_a_copy_effect(
    library: Library, qapp: QApplication
) -> None:
    from PySide6.QtGui import QGuiApplication

    assert library.copyToClipboard("Study/notes.txt") == ""
    mime = QGuiApplication.clipboard().mimeData()
    assert mime is not None and mime.urls()[0].toLocalFile().endswith("notes.txt")
    assert mime.hasFormat("Preferred DropEffect")
    assert library.copyToClipboard("Study/none.txt") == "Nothing to copy"
    assert library.pasteInto("Study") == ""  # pastes the copied file next to itself
    assert "notes (2).txt" in names(library, "Study")
