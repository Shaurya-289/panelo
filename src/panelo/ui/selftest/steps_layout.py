"""Self-test steps for the rearrangeable overlay layout: drag and drop, the picker, the
splitters and saving.  Ported from the prototype (round 4)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from panelo.ui.selftest.driver import Step
from panelo.ui.selftest.unit_checks import (  # noqa: F401
    GAP,
    MIN_H,
    MIN_W,
    PANELS,
    RECT,
    close,
)


def layout_steps(env: Any) -> list[Step]:
    """Real mouse and key events for the drag-and-drop, picker, splitter and persistence checks."""
    controller = env.controller
    overlay, full = controller.overlay, controller.full
    left = Qt.MouseButton.LeftButton
    no_mod = Qt.KeyboardModifier.NoModifier
    memo: dict = {}
    tag = "layout"

    def js(value):
        return value.toVariant() if hasattr(value, "toVariant") else value

    def at(x: float, y: float) -> QPoint:
        return QPoint(round(x), round(y))

    def probe(name: str, index: int = 0) -> QPoint:
        v = js(overlay.probe(name, index))
        return at(v["x"], v["y"])

    def point_in(key: str, fx: float, fy: float) -> QPoint:
        v = js(overlay.pointIn(key, fx, fy))
        return at(v["x"], v["y"])

    def header(key: str) -> QPoint:
        v = js(overlay.headerPoint(key))
        return at(v["x"], v["y"])

    def slot_point(tpl: int, slot: int) -> QPoint:
        v = js(overlay.snapSlotPoint(tpl, slot))
        return at(v["x"], v["y"])

    def rects() -> dict:
        return json.loads(overlay.property("rectsText"))

    def geometry(key: str) -> dict:
        return json.loads(overlay.panelGeometry(key))

    def layout_text() -> str:
        return str(overlay.property("layoutText"))

    def default_text() -> str:
        return json.dumps(json.loads(overlay.layoutCall("defaultTree", "[]")), separators=(",", ":"))

    def settled() -> bool:
        """Every panel sits on its rectangle (the glide has finished)."""
        r = rects()
        return all(close(geometry(k), r[k], 1.5) for k in PANELS)

    def dragging() -> bool:
        return bool(overlay.property("dragging"))

    def press(p: QPoint) -> None:
        QTest.mousePress(overlay, left, no_mod, p)

    def move(p: QPoint) -> None:
        QTest.mouseMove(overlay, p)

    def release(p: QPoint) -> None:
        QTest.mouseRelease(overlay, left, no_mod, p)

    def reset() -> None:
        overlay.resetLayout()

    def remember() -> None:
        memo["layout"] = layout_text()
        memo["rects"] = rects()

    def begin_drag(key: str) -> None:
        memo["from"] = header(key)
        press(memo["from"])

    def cross_threshold() -> None:
        p = memo["from"]
        move(at(p.x() + 25, p.y() + 25))

    def hold_over(key: str, fx: float, fy: float) -> Callable[[], None]:
        def go() -> None:
            memo["to"] = point_in(key, fx, fy)
            move(memo["to"])
        return go

    def drop() -> None:
        release(memo["to"])

    def drag_to(key: str, target: str, fx: float, fy: float) -> list:
        """Press the header of `key`, lift it and hold it over a spot of `target` (no release)."""
        return [
            (0, lambda: begin_drag(key), None),
            (40, cross_threshold, None),
            (80, hold_over(target, fx, fy), None),
            (120, None, None),
        ]

    def tasks_done() -> list:
        return [bool(t["done"]) for t in js(controller.store.property("tasks"))]

    def equal_widths() -> bool:
        widths = [rects()[k]["width"] for k in PANELS]
        xs = sorted(rects()[k]["x"] for k in PANELS)
        return max(widths) - min(widths) < 1 and xs == sorted(xs) and len(set(round(x) for x in xs)) == 5

    def saved_text() -> str:
        """The layout as a restarted app would read it: from the saved settings."""
        env.backend.flush_settings()
        return str(env.application.settings.get_settings().overlay_layout_json)

    def saved_layout() -> dict:
        text = saved_text()
        return json.loads(text) if text else {}

    def reload_from_file() -> bool:
        before = layout_text()
        overlay.loadLayout(saved_text())
        return layout_text() == before

    def splitter_drag(dx: float) -> Callable[[], None]:
        def go() -> None:
            move(at(memo["split_from"].x() + dx, memo["split_from"].y()))
        return go

    def start_splitter() -> None:
        memo["split_from"] = probe("splitterRoot", 0)
        press(memo["split_from"])

    def end_splitter() -> None:
        release(memo["split_from"])

    return [
        (0, controller.open_overlay, None),
        (600, reset, None),
        (300, remember, None),
        # ── swap: Notes onto the middle of Timers ──
        *drag_to("notes", "timers", 0.5, 0.55),
        (0, None, (f"{tag}: dragging a header lifts the panel, shows a swap preview and the snap bar",
                   lambda: dragging() and overlay.property("previewKind") == "swap"
                   and overlay.property("previewValid") is True and overlay.property("snapBarShown") is True)),
        (0, drop, None),
        (450, None, (f"{tag}: dropping Notes on the middle of Timers swaps them (rects)",
                     lambda: not dragging() and close(rects()["notes"], memo["rects"]["timers"])
                     and close(rects()["timers"], memo["rects"]["notes"])
                     and close(rects()["todo"], memo["rects"]["todo"]))),
        (0, None, (f"{tag}: the panels glide to their new rectangles", settled)),
        # ── split: To-Do onto the right edge of Files ──
        (0, reset, None),
        (400, remember, None),
        *drag_to("todo", "files", 0.97, 0.5),
        (0, None, (f"{tag}: over an edge band the preview is a valid split",
                   lambda: overlay.property("previewKind") == "split" and overlay.property("previewZone") == "right"
                   and overlay.property("previewValid") is True)),
        (0, drop, None),
        (450, None, (f"{tag}: dropping To-Do on the right edge of Files splits it (To-Do right of Files, same row)",
                     lambda: rects()["todo"]["x"] >= rects()["files"]["x"] + rects()["files"]["width"]
                     and abs(rects()["todo"]["y"] - rects()["files"]["y"]) < 1
                     and abs(rects()["todo"]["height"] - rects()["files"]["height"]) < 1
                     and rects()["files"]["width"] >= 219.5 and settled())),
        # ── minimum size: five columns, then split a column in two ──
        (0, reset, None),
        (350, lambda: overlay.applyTemplateNamed("columns"), None),
        (450, remember, None),
        *drag_to("notes", "reminders", 0.97, 0.5),
        (0, None, (f"{tag}: a split that breaks the minimum size previews as invalid",
                   lambda: overlay.property("previewKind") == "split" and overlay.property("previewValid") is False)),
        (0, drop, None),
        (450, None, (f"{tag}: the invalid drop is refused: layout unchanged, panel glided back",
                     lambda: not dragging() and layout_text() == memo["layout"] and settled())),
        # ── Esc mid-drag ──
        (0, reset, None),
        (400, remember, None),
        *drag_to("notes", "timers", 0.5, 0.55),
        (0, lambda: QTest.keyClick(overlay, Qt.Key.Key_Escape), None),
        (60, None, (f"{tag}: Esc mid-drag cancels the drag (overlay stays open)",
                    lambda: not dragging() and overlay.property("previewKind") == ""
                    and controller.overlay_state == "open")),
        (0, drop, None),
        (450, None, (f"{tag}: ...the panel glides back and the release does not drop it",
                     lambda: layout_text() == memo["layout"] and settled())),
        # ── double-click still morphs ──
        (0, lambda: QTest.mouseDClick(overlay, left, no_mod, header("todo")), None),
        (1200, None, (f"{tag}: a double-click on a header still morphs into the Full App (section tasks)",
                      lambda: controller.full_visible() and full.property("section") == "tasks"
                      and controller.overlay_state == "hidden")),
        (0, controller.hide_full, None),
        (0, controller.open_overlay, None),
        (600, remember, None),
        (0, lambda: QTest.mouseClick(overlay, left, no_mod, header("todo")), None),
        (300, None, (f"{tag}: a plain click on a header does nothing",
                     lambda: controller.overlay_state == "open" and layout_text() == memo["layout"])),
        # ── the picker button and its popup ──
        (0, lambda: QTest.mouseClick(overlay, left, no_mod, probe("layoutButton")), None),
        (300, None, (f"{tag}: the picker button opens the popup", lambda: overlay.property("layoutPopupOpen") is True)),
        (0, lambda: QTest.mouseClick(overlay, left, no_mod, probe("layoutCardOutside")), None),
        (150, None, (f"{tag}: a click on empty popup card space does not close it",
                     lambda: overlay.property("layoutPopupOpen") is True)),
        (0, lambda: memo.update(done=tasks_done()), None),
        (0, lambda: QTest.mouseClick(overlay, left, no_mod, probe("todoCheck", 0)), None),
        (300, None, (f"{tag}: click-through: clicking outside the popup closes it and does not toggle the checkbox under it",
                     lambda: overlay.property("layoutPopupOpen") is False and tasks_done() == memo["done"])),
        (0, lambda: QTest.mouseClick(overlay, left, no_mod, probe("layoutButton")), None),
        (300, lambda: QTest.mouseClick(overlay, left, no_mod, probe("layoutThumb", 3)), None),
        (600, None, (f"{tag}: clicking 'Five columns' applies it: 5 equal widths, popup closed",
                     lambda: overlay.property("layoutPopupOpen") is False and equal_widths() and settled())),
        # ── the snap bar: drop on slot 1 of "One big focus" ──
        (0, reset, None),
        (400, lambda: begin_drag("reminders"), None),
        (40, cross_threshold, None),
        (300, None, (f"{tag}: during a drag the snap bar slides in at the top",
                     lambda: overlay.property("snapBarShown") is True and dragging())),
        (0, lambda: (memo.update(to=slot_point(1, 0)), move(memo["to"])), None),
        (100, None, (f"{tag}: over a bar slot there is no panel preview", lambda: overlay.property("previewKind") == "")),
        (0, drop, None),
        (500, None, (f"{tag}: dropping on slot 1 of 'One big focus' puts the dragged panel in the big slot",
                     lambda: rects()["reminders"]["width"] > 0.55 * (rects()["todo"]["x"] + rects()["todo"]["width"])
                     and rects()["reminders"]["height"] > 0.9 * float(overlay.property("groupH")) and not dragging() and settled())),
        # ── persistence ──
        (800, None, (f"{tag}: the layout is saved to the settings file and reads back as the same tree",
                     lambda: saved_layout() == json.loads(layout_text()) and reload_from_file())),
        # ── reset ──
        (0, lambda: QTest.mouseClick(overlay, left, no_mod, probe("layoutButton")), None),
        (300, lambda: QTest.mouseClick(overlay, left, no_mod, probe("layoutReset")), None),
        (500, None, (f"{tag}: 'Reset to default' restores the sketch",
                     lambda: json.loads(layout_text()) == json.loads(default_text()) and settled() and overlay.property("layoutPopupOpen") is False)),
        (800, None, (f"{tag}: ...and the saved layout is the sketch again", lambda: saved_layout() == json.loads(default_text()))),
        # ── splitters ──
        (0, remember, None),
        (0, start_splitter, None),
        (30, splitter_drag(-60), None),
        (30, splitter_drag(-90), None),
        (30, None, (f"{tag}: a splitter drag resizes live (Notes narrower, To-Do wider)",
                    lambda: rects()["notes"]["width"] < memo["rects"]["notes"]["width"] - 80
                    and rects()["todo"]["width"] > memo["rects"]["todo"]["width"] + 80
                    and close(geometry("notes"), rects()["notes"], 0.5))),
        (0, splitter_drag(-2000), None),
        (30, None, (f"{tag}: ...and clamps at the minimum size",
                    lambda: abs(rects()["notes"]["width"] - MIN_W) < 0.5)),
        (0, end_splitter, None),
        (100, None, (f"{tag}: ...a drag past the limit keeps every panel at least {MIN_W}x{MIN_H}",
                     lambda: all(rects()[k]["width"] >= MIN_W - 0.5 and rects()[k]["height"] >= MIN_H - 0.5 for k in PANELS))),
        (0, reset, None),
        (500, lambda: controller.close_overlay(), None),
        (400, None, None),
    ]
