import json

import pytest

from productivity_app.desktop.ui.layout_store import LayoutStore
from productivity_app.desktop.ui.workspace_state import default_layout


def test_atomic_layout_and_bad_input(tmp_path):
    store = LayoutStore(tmp_path / "ui" / "workspace.json")
    store.save(default_layout())
    assert store.load() == default_layout()
    store.path.write_text('{"version": true}', encoding="utf-8")
    assert store.load() == default_layout() and store.diagnostic





@pytest.mark.parametrize(
    "change", ["version", "missing", "unknown", "boolean", "overlap", "small", "float", "bounds"]
)
def test_invalid_layouts_fall_back_without_overwrite(tmp_path, change):
    store = LayoutStore(tmp_path / "workspace.json")
    store.save(default_layout())
    data = json.loads(store.path.read_text())
    if change == "version":
        data["version"] = 9
    elif change == "missing":
        del data["panels"]["notes"]
    elif change == "unknown":
        data["panels"]["other"] = data["panels"]["notes"]
    elif change == "boolean":
        data["panels"]["notes"]["x"] = False
    elif change == "overlap":
        data["panels"]["todo"]["x"] = 0
    elif change == "small":
        data["panels"]["notes"]["w"] = 1
    elif change == "float":
        data["panels"]["notes"]["w"] = 507.0
    else:
        data["panels"]["notes"]["x"] = 999999
    raw = json.dumps(data)
    store.path.write_text(raw)
    assert store.load() == default_layout() and store.diagnostic
    assert store.path.read_text() == raw


def test_malformed_duplicate_oversized_and_atomic_failure(tmp_path, monkeypatch):
    store = LayoutStore(tmp_path / "workspace.json")
    for raw in ("not json", "[]", '{"version":1,"version":1}', "x" * 65537):
        store.path.write_text(raw)
        assert store.load() == default_layout() and store.diagnostic
    from productivity_app.desktop.ui import layout_store

    class FailedFile:
        def __init__(self, path):
            pass

        def setDirectWriteFallback(self, value):
            assert value is False

        def open(self, mode):
            return False

        def errorString(self):
            return "Denied"

    monkeypatch.setattr(layout_store, "QSaveFile", FailedFile)
    with pytest.raises(OSError):
        store.save(default_layout())
    assert store.path.read_text() == "x" * 65537
