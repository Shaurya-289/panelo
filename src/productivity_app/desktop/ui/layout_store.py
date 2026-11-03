"""Atomic UI-only layout storage. Bad files are never overwritten on load."""

import json
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile

from productivity_app.desktop.ui.workspace_geometry import valid
from productivity_app.desktop.ui.workspace_state import Rect, default_layout


class LayoutStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.diagnostic = ""

    def load(self) -> dict[str, Rect]:
        if not self.path.exists():
            return default_layout()
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError("Layout file too large")
            with self.path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("Layout file too large")

            def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON key")
                    result[key] = value
                return result

            data = json.loads(raw, object_pairs_hook=unique)
            if (
                data["version"] != 1
                or type(data["version"]) is not int
                or data["width"] != 1180
                or data["height"] != 660
                or type(data["width"]) is not int
                or type(data["height"]) is not int
            ):
                raise ValueError("Unsupported layout format")
            result = {key: Rect(**value) for key, value in data["panels"].items()}
            if not valid(result):
                raise ValueError("Invalid tile geometry")
            return result
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.diagnostic = f"Saved layout rejected: {exc}"
            return default_layout()

    def save(self, layout: dict[str, Rect]) -> None:
        if not valid(layout):
            raise ValueError("Only valid completed layouts can be saved")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            {
                "version": 1,
                "width": 1180,
                "height": 660,
                "panels": {key: asdict(value) for key, value in layout.items()},
            }
        ).encode()
        file = QSaveFile(str(self.path))
        file.setDirectWriteFallback(False)
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(file.errorString())
        if file.write(data) != len(data) or not file.commit():
            file.cancelWriting()
            raise OSError("Layout not saved")
