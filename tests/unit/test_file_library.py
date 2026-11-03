"""The file library (pure Python half): everything runs under tmp_path with a fake trash.

Nothing here touches the real Recycle Bin, the real library folder or any real user file.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from platformdirs import user_data_dir

from productivity_app.files.library import (
    FileLibrary,
    default_library_root,
    human_size,
    human_time,
    kind_of,
    library_root,
    unique_path,
    valid_name,
)


class FakeTrash:
    """Stands in for the Recycle Bin: records the path, then deletes it for good."""

    def __init__(self) -> None:
        self.trashed: list[Path] = []
        self.result = True

    def __call__(self, path: Path) -> bool:
        if not self.result:
            return False
        self.trashed.append(path)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        return True


@pytest.fixture
def trash() -> FakeTrash:
    return FakeTrash()


@pytest.fixture
def library(tmp_path: Path, trash: FakeTrash) -> FileLibrary:
    lib = FileLibrary(tmp_path / "lib", trash=trash)
    for folder in ("Study/Physics", "Study/Maths", "Study/Chemistry", "Coding", "Personal"):
        (lib.root / folder).mkdir(parents=True)
    (lib.root / "Study/Physics/Chapter 3 - Kinematics.pdf").write_bytes(b"%PDF-1.4 fake" + b"x" * 2048)
    (lib.root / "Study/Physics/Lab graph.png").write_bytes(b"png" * 100)
    (lib.root / "Study/Maths/Formulas.md").write_text("# Formulas\n", encoding="utf-8")
    (lib.root / "Coding/ideas.txt").write_text("ideas", encoding="utf-8")
    (lib.root / "Personal/Trip budget.csv").write_text("Item,Cost\n", encoding="utf-8")
    return lib


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A file next to the library that no operation may ever touch."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "notes.txt").write_text("hello", encoding="utf-8")
    return source


def names(library: FileLibrary, rel: str) -> list[str]:
    return [entry["name"] for entry in library.list_dir(rel)]


# -- creation and the root ------------------------------------------------------------------------
def test_the_root_is_created_and_resolved(tmp_path: Path, trash: FakeTrash) -> None:
    root = tmp_path / "a" / "b" / "lib"
    library = FileLibrary(root, trash=trash)
    assert root.is_dir() and library.root == root.resolve()
    assert library.list_dir("") == []


def test_the_default_root_is_files_inside_the_application_data_folder() -> None:
    root = default_library_root()
    assert root.name == "Files"
    assert root.parent == Path(user_data_dir("ProductivityApp", "ProductivityApp"))
    assert library_root("") == root == library_root("   ")
    assert library_root(" D:/Notes/Files ") == Path("D:/Notes/Files")


# -- reading --------------------------------------------------------------------------------------
def test_list_returns_folders_first_then_names_case_insensitively(library: FileLibrary) -> None:
    (library.root / "Study/zeta.txt").write_text("z", encoding="utf-8")
    (library.root / "Study/Alpha.txt").write_text("a", encoding="utf-8")
    assert names(library, "") == ["Coding", "Personal", "Study"]
    assert names(library, "Study") == ["Chemistry", "Maths", "Physics", "Alpha.txt", "zeta.txt"]
    assert names(library, "Study/Physics") == ["Chapter 3 - Kinematics.pdf", "Lab graph.png"]


def test_entries_carry_the_fields_the_ui_reads(library: FileLibrary) -> None:
    pdf, png = library.list_dir("Study/Physics")
    assert pdf["kind"] == "pdf" and pdf["ext"] == "pdf" and not pdf["isDir"]
    assert pdf["rel"] == "Study/Physics/Chapter 3 - Kinematics.pdf"
    assert pdf["size"].endswith(" KB") and pdf["url"].startswith("file:///")
    assert pdf["modified"].startswith("Today ")  # written moments ago
    assert pdf["items"] == 0
    assert png["kind"] == "image"
    folder = library.list_dir("Study")[2]
    assert (folder["name"], folder["isDir"], folder["kind"], folder["ext"], folder["size"]) == (
        "Physics", True, "folder", "", "",
    )
    assert folder["items"] == 2 and folder["rel"] == "Study/Physics"


def test_hidden_entries_are_never_listed_counted_searched_or_totalled(library: FileLibrary) -> None:
    folder = library.root / "Personal"
    for hidden in (".secret", "desktop.ini", "Thumbs.db", "THUMBS.DB"):
        (folder / hidden).write_text("x" * 100, encoding="utf-8")
    (folder / ".hidden-dir").mkdir()
    (folder / ".hidden-dir" / "inner.txt").write_text("inner", encoding="utf-8")
    assert names(library, "Personal") == ["Trip budget.csv"]
    assert library.list_dir("")[1]["items"] == 1
    assert library.search("inner") == [] and library.search("secret") == []
    assert library.usage() == library.usage()
    assert ".hidden-dir" not in library.folders()
    assert library.usage().endswith(", 5 files")


def test_listing_a_missing_folder_or_a_file_gives_nothing(library: FileLibrary) -> None:
    assert library.list_dir("Nope") == []
    assert library.list_dir("Coding/ideas.txt") == []


def test_search_is_case_insensitive_and_lists_folders_first(library: FileLibrary) -> None:
    (library.root / "Study/Physics/Kinematics notes").mkdir()
    found = library.search("KINEMAT")
    assert [(item["name"], item["parent"]) for item in found] == [
        ("Kinematics notes", "Study/Physics"),
        ("Chapter 3 - Kinematics.pdf", "Study/Physics"),
    ]
    assert found[0]["isDir"] and not found[1]["isDir"]
    assert library.search("   ") == [] and library.search("") == []
    assert library.search("zzz-nothing") == []


def test_search_is_limited_to_two_hundred_results(library: FileLibrary) -> None:
    bulk = library.root / "bulk"
    bulk.mkdir()
    for number in range(250):
        (bulk / f"match {number:03}.txt").write_text("", encoding="utf-8")
    assert len(library.search("match")) == 200


def test_folders_lists_every_folder_as_a_relative_path(library: FileLibrary) -> None:
    assert library.folders() == [
        "Coding", "Personal", "Study", "Study/Chemistry", "Study/Maths", "Study/Physics",
    ]
    assert library.is_dir("Study/Physics") and not library.is_dir("Coding/ideas.txt")
    assert not library.is_dir("nope") and not library.is_dir("..")


def test_usage_counts_files_and_sizes(tmp_path: Path, trash: FakeTrash) -> None:
    library = FileLibrary(tmp_path / "usage", trash=trash)
    assert library.usage() == "0 B, 0 files"
    (library.root / "a.txt").write_bytes(b"x" * 1500)
    assert library.usage() == "1.5 KB, 1 file"
    (library.root / "sub").mkdir()
    (library.root / "sub" / "b.bin").write_bytes(b"y" * (2 * 1024 * 1024))
    assert library.usage() == "2.0 MB, 2 files"


def test_unique_folder_name_avoids_existing_names(library: FileLibrary) -> None:
    assert library.unique_folder_name("Coding") == "New folder"
    (library.root / "Coding/New folder").mkdir()
    (library.root / "Coding/New folder (2)").mkdir()
    assert library.unique_folder_name("Coding") == "New folder (3)"
    assert library.unique_folder_name("../outside") == "New folder"


# -- helpers --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.pdf", "pdf"), ("A.PDF", "pdf"), ("deck.pptx", "slides"), ("notes.md", "doc"),
        ("sheet.XLSX", "sheet"), ("photo.JPG", "image"), ("clip.mkv", "video"), ("song.flac", "audio"),
        ("bundle.zip", "archive"), ("main.py", "code"), ("noext", "other"), ("weird.xyz", "other"),
        (".hidden", "other"),
    ],
)
def test_kind_of(name: str, kind: str) -> None:
    assert kind_of(name) == kind


def test_human_size() -> None:
    assert [human_size(n) for n in (0, 1, 1023, 1024, 1536, 1024**2, 5 * 1024**3, 3000 * 1024**3)] == [
        "0 B", "1 B", "1023 B", "1.0 KB", "1.5 KB", "1.0 MB", "5.0 GB", "3000.0 GB",
    ]


def test_human_time_names_today_and_omits_the_current_year() -> None:
    now = datetime(2026, 11, 3, 15, 0)
    assert human_time(datetime(2026, 11, 3, 9, 5).timestamp(), now) == "Today 09:05"
    assert human_time(datetime(2026, 10, 28, 9, 5).timestamp(), now) == "Oct 28"
    assert human_time((now - timedelta(days=400)).timestamp(), now) == "Sep 29 2025"


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b", "ends."])
def test_invalid_names(name: str) -> None:
    assert valid_name(name) != ""


@pytest.mark.parametrize("name", ["notes.txt", "My folder", " padded ", ".gitignore", "a-b_c (1)", "naïve – café"])
def test_valid_names(name: str) -> None:
    assert valid_name(name) == ""


def test_unique_path_numbers_files_and_folders(tmp_path: Path) -> None:
    assert unique_path(tmp_path, "a.txt") == tmp_path / "a.txt"
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "a (2).txt").write_text("")
    assert unique_path(tmp_path, "a.txt") == tmp_path / "a (3).txt"
    (tmp_path / "docs.v2").mkdir()
    assert unique_path(tmp_path, "docs.v2") == tmp_path / "docs.v2 (2)"  # a folder keeps its whole name
    (tmp_path / "noext").write_text("")
    assert unique_path(tmp_path, "noext") == tmp_path / "noext (2)"


# -- adding files ---------------------------------------------------------------------------------
def test_add_files_copies_and_leaves_the_original(library: FileLibrary, outside: Path) -> None:
    assert library.add_files([outside / "notes.txt"], "Coding") == ""
    assert (library.root / "Coding/notes.txt").read_text(encoding="utf-8") == "hello"
    assert (outside / "notes.txt").exists()


def test_a_name_clash_gets_a_numbered_suffix(library: FileLibrary, outside: Path) -> None:
    for _ in range(3):
        assert library.add_files([str(outside / "notes.txt")], "Coding") == ""
    assert names(library, "Coding") == ["ideas.txt", "notes (2).txt", "notes (3).txt", "notes.txt"]


def test_a_folder_is_copied_with_its_contents_and_clashes_get_numbered(library: FileLibrary, outside: Path) -> None:
    (outside / "pack").mkdir()
    (outside / "pack" / "one.txt").write_text("1", encoding="utf-8")
    assert library.add_files([outside / "pack"], "") == ""
    assert library.add_files([outside / "pack"], "") == ""
    assert (library.root / "pack/one.txt").read_text(encoding="utf-8") == "1"
    assert (library.root / "pack (2)/one.txt").exists()


def test_add_files_reports_problems_per_item(library: FileLibrary, outside: Path) -> None:
    message = library.add_files([outside / "gone.txt", outside / "notes.txt", ""], "Coding")
    assert message == "gone.txt no longer exists"
    assert (library.root / "Coding/notes.txt").exists()
    assert library.add_files([], "Coding") == "Nothing to add"
    assert library.add_files([""], "Coding") == "Nothing to add"
    assert library.add_files([outside / "notes.txt"], "No/such/folder") == "Destination folder doesn't exist"
    assert library.add_files([outside / "notes.txt"], "Coding/ideas.txt") == "Destination folder doesn't exist"
    both = library.add_files([outside / "a-gone", outside / "b-gone"], "Coding")
    assert both == "a-gone no longer exists; b-gone no longer exists"


def test_a_folder_cannot_be_copied_into_itself(library: FileLibrary) -> None:
    message = library.add_files([library.root / "Study"], "Study/Physics")
    assert message == "Can't copy Study into itself"
    assert names(library, "Study/Physics") == ["Chapter 3 - Kinematics.pdf", "Lab graph.png"]


# -- create, rename, move, remove -----------------------------------------------------------------
def test_create_folder(library: FileLibrary) -> None:
    assert library.create_folder("Coding", "Scratch") == ""
    assert (library.root / "Coding/Scratch").is_dir()
    assert library.create_folder("Coding", "  ") == ""  # blank means the default name
    assert (library.root / "Coding/New folder").is_dir()
    assert library.create_folder("Coding", "Scratch") == ""
    assert (library.root / "Coding/Scratch (2)").is_dir()
    assert library.create_folder("Coding", "a/b") != ""
    assert library.create_folder("Coding", "bad?") != ""
    assert library.create_folder("Coding", "trailing.") != ""
    assert library.create_folder("Missing/parent", "x") != ""


def test_rename(library: FileLibrary) -> None:
    library.create_folder("Coding", "Scratch")
    assert library.rename("Coding/Scratch", "Projects") == ""
    assert (library.root / "Coding/Projects").is_dir() and not (library.root / "Coding/Scratch").exists()
    assert library.rename("Coding/Projects", "a/b") != ""
    assert library.rename("Coding/Projects", "  ") != ""
    assert library.rename("Coding/Projects", "ideas.txt") == "\u201cideas.txt\u201d already exists here"
    assert library.rename("Coding/Projects", "Projects") == ""  # unchanged name
    assert library.rename("Coding/Projects", "  Padded  ") == ""
    assert (library.root / "Coding/Padded").is_dir()
    assert library.rename("", "x") == "Can't rename the library"
    assert library.rename("Coding/ideas.txt", "IDEAS.txt") == ""  # only the case changes
    assert "IDEAS.txt" in names(library, "Coding")
    assert library.rename("Coding/missing", "x") != ""


def test_move(library: FileLibrary) -> None:
    assert library.move("Coding/ideas.txt", "Personal") == ""
    assert (library.root / "Personal/ideas.txt").exists() and not (library.root / "Coding/ideas.txt").exists()
    # a clash keeps both
    (library.root / "Coding/ideas.txt").write_text("again", encoding="utf-8")
    assert library.move("Coding/ideas.txt", "Personal") == ""
    assert (library.root / "Personal/ideas (2).txt").read_text(encoding="utf-8") == "again"
    # moving to where it already is changes nothing
    assert library.move("Personal/ideas.txt", "Personal") == ""
    assert names(library, "Personal").count("ideas.txt") == 1
    assert library.move("Study", "") == ""  # to the root, where it already lives


def test_move_refusals(library: FileLibrary) -> None:
    assert library.move("Study", "Study/Physics") == "Can't move a folder into itself"
    assert library.move("Study", "Study") == "Can't move a folder into itself"
    assert library.move("", "Coding") == "Nothing to move"
    assert library.move("Coding/missing", "Personal") == "Nothing to move"
    assert library.move("Coding/ideas.txt", "Coding/missing") == "Destination folder doesn't exist"
    assert library.move("Coding/ideas.txt", "Personal/Trip budget.csv") == "Destination folder doesn't exist"
    assert (library.root / "Study/Physics").is_dir() and (library.root / "Coding/ideas.txt").exists()


def test_remove_hands_the_path_to_the_trash(library: FileLibrary, trash: FakeTrash) -> None:
    assert library.remove("Personal/Trip budget.csv") == ""
    assert library.remove("Coding") == ""
    assert trash.trashed == [library.root / "Personal/Trip budget.csv", library.root / "Coding"]
    assert not (library.root / "Coding").exists()


def test_remove_reports_what_could_not_be_removed(library: FileLibrary, trash: FakeTrash) -> None:
    assert library.remove("") == "Nothing to delete"
    assert library.remove("Coding/missing.txt") == "Nothing to delete"
    trash.result = False
    assert library.remove("Coding/ideas.txt") == "Couldn't move it to the Recycle Bin"
    assert (library.root / "Coding/ideas.txt").exists()

    def broken(_path: Path) -> bool:
        raise PermissionError(13, "Access is denied")

    failing = FileLibrary(library.root, trash=broken)
    assert failing.remove("Coding/ideas.txt").startswith("Couldn't delete: ")


# -- the path guard -------------------------------------------------------------------------------
ESCAPES = ["..", "../..", "../src", "Study/../../src", "..\\..", "C:/Windows", "C:\\Windows", "Coding/C:", "a:b"]


@pytest.mark.parametrize("rel", ESCAPES)
def test_paths_that_leave_the_library_are_rejected(library: FileLibrary, outside: Path, rel: str) -> None:
    with pytest.raises(ValueError, match="outside the library"):
        library.resolve(rel)
    assert library.list_dir(rel) == []
    assert not library.is_dir(rel)
    assert library.unique_folder_name(rel) == "New folder"
    assert library.remove(rel + "/notes.txt") != ""
    assert library.rename(rel, "x") != ""
    assert library.create_folder(rel, "x") != ""
    assert library.move("Coding/ideas.txt", rel) != ""
    assert library.move(rel, "Coding") != ""
    assert library.add_files([outside / "notes.txt"], rel) != ""
    assert (outside / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert (library.root / "Coding/ideas.txt").exists()


def test_inside_paths_resolve_with_either_slash_and_stray_separators(library: FileLibrary) -> None:
    assert library.resolve("Study/Physics") == library.root / "Study" / "Physics"
    assert library.resolve("Study\\Physics") == library.root / "Study" / "Physics"
    assert library.resolve("/Study/Physics/") == library.root / "Study" / "Physics"
    assert library.resolve("") == library.root == library.resolve("/")
    # a leading slash does not mean the drive root: it is still relative to the library
    assert library.resolve("/etc") == library.root / "etc"
    assert library.resolve("//server/share") == library.root / "server" / "share"
    assert library.resolve("Study/Physics/../Maths") == library.root / "Study" / "Maths"


def test_a_symlink_pointing_outside_cannot_be_followed(library: FileLibrary, outside: Path) -> None:
    link = library.root / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    with pytest.raises(ValueError, match="outside the library"):
        library.resolve("escape")
    assert library.list_dir("escape/") == []
    assert library.remove("escape/notes.txt") != ""
    assert (outside / "notes.txt").exists()


# -- the library location ---------------------------------------------------------------------------
def test_check_new_root(library: FileLibrary, tmp_path: Path) -> None:
    assert library.check_new_root("") == "Choose a folder"
    assert library.check_new_root("   ") == "Choose a folder"
    assert library.check_new_root(str(library.root)) == "That is already the library folder"
    assert library.check_new_root(str(library.root / "Study")) == "Choose a folder outside the current library"
    assert library.check_new_root(str(library.root.parent)) == "Choose a folder outside the current library"
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    assert library.check_new_root(str(tmp_path / "a-file")) == "That is a file, not a folder"
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.txt").write_text("x", encoding="utf-8")
    assert "empty or new" in library.check_new_root(str(busy))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert library.check_new_root(str(empty)) == ""
    assert library.check_new_root(str(tmp_path / "does" / "not" / "exist")) == ""
    assert library.check_new_root(f"  {empty}  ") == ""


def test_move_to_moves_everything_and_switches_the_root(library: FileLibrary, tmp_path: Path) -> None:
    original = library.root
    target = tmp_path / "new place" / "library"
    assert library.move_to(str(target)) == ""
    assert library.root == target.resolve()
    assert names(library, "") == ["Coding", "Personal", "Study"]
    assert (target / "Study/Physics/Chapter 3 - Kinematics.pdf").read_bytes().startswith(b"%PDF")
    assert not any(original.iterdir())
    # and back again into the now-empty original folder
    assert library.move_to(str(original)) == ""
    assert library.root == original.resolve()
    assert (original / "Study/Physics").is_dir() and not any(target.iterdir())


def test_move_to_refuses_a_busy_folder_and_changes_nothing(library: FileLibrary, tmp_path: Path) -> None:
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.txt").write_text("x", encoding="utf-8")
    before = library.folders()
    assert "empty or new" in library.move_to(str(busy))
    assert library.root.name == "lib" and library.folders() == before
    assert library.move_to("") == "Choose a folder"
    assert library.move_to(str(library.root / "Study")) == "Choose a folder outside the current library"
    assert names(library, "") == ["Coding", "Personal", "Study"]


def test_move_to_reports_a_failure_to_move(library: FileLibrary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(shutil, "move", refuse)
    message = library.move_to(str(tmp_path / "target"))
    assert message.startswith("Couldn't move the files: ")
    assert library.root.name == "lib"  # still the old root
