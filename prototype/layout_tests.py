"""Self-test checks for the rearrangeable overlay layout (round 4).

`unit_checks` calls the real qml/Proto/layout.js through the overlay's `layoutCall` helper, so the
library that runs in the app is the one under test. `ui_steps` drives the overlay with real
QTest mouse and key events. Both are used by `main.py --selftest`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest

PANELS = ["notes", "todo", "files", "timers", "reminders"]
RECT = {"x": 0, "y": 0, "width": 1632, "height": 918}
GAP = 10
MIN_W, MIN_H = 220, 160


def close(a: dict, b: dict, eps: float = 1.0) -> bool:
    return all(abs(a[k] - b[k]) <= eps for k in ("x", "y", "width", "height"))


def unit_checks(check: Callable[[str, bool], None], overlay) -> None:  # noqa: ANN001
    def call(fn: str, *args):  # noqa: ANN202
        return json.loads(overlay.layoutCall(fn, json.dumps(list(args))))

    def rects_of(tree: dict, rect: dict = RECT) -> dict:
        return call("computeRects", tree, rect, GAP)["rects"]

    default = call("defaultTree")
    check("layout.js: the default tree is valid", call("validate", default) is True)

    # The old fixed layout, as the formulas it used.
    avail = RECT["width"] - 2 * GAP
    w_left, w_mid = avail * 0.30, avail * 0.40
    w_right = avail - w_left - w_mid
    col_h = RECT["height"] - GAP
    todo_h, timers_h = col_h * 0.38, col_h * 0.5
    mid_x, right_x = w_left + GAP, w_left + w_mid + 2 * GAP
    old = {
        "notes": {"x": 0, "y": 0, "width": w_left, "height": RECT["height"]},
        "todo": {"x": mid_x, "y": 0, "width": w_mid, "height": todo_h},
        "files": {"x": mid_x, "y": todo_h + GAP, "width": w_mid, "height": col_h - todo_h},
        "timers": {"x": right_x, "y": 0, "width": w_right, "height": timers_h},
        "reminders": {"x": right_x, "y": timers_h + GAP, "width": w_right, "height": col_h - timers_h},
    }
    now = rects_of(default)
    check("layout.js: default rects match the old fixed layout within 1px",
          all(close(now[k], old[k]) for k in PANELS))

    swapped = rects_of(call("swap", default, "notes", "timers"))
    check("layout.js: swap exchanges two panels' rectangles",
          close(swapped["notes"], now["timers"]) and close(swapped["timers"], now["notes"])
          and close(swapped["todo"], now["todo"]))

    left = call("splitAt", default, "files", "notes", "left")
    r = rects_of(left)
    left_ok = (call("validate", left) and r["notes"]["x"] < r["files"]["x"]
               and abs(r["notes"]["y"] - r["files"]["y"]) < 1
               and abs(r["notes"]["height"] - r["files"]["height"]) < 1
               and abs(r["notes"]["width"] - r["files"]["width"]) < 1)
    top = call("splitAt", default, "timers", "reminders", "top")
    r = rects_of(top)
    top_ok = (call("validate", top) and r["reminders"]["y"] < r["timers"]["y"]
              and abs(r["reminders"]["x"] - r["timers"]["x"]) < 1
              and abs(r["reminders"]["height"] - r["timers"]["height"]) < 1)
    right = call("splitAt", default, "files", "todo", "right")
    r = rects_of(right)
    right_ok = (call("validate", right) and r["todo"]["x"] >= r["files"]["x"] + r["files"]["width"]
                and abs(r["todo"]["y"] - r["files"]["y"]) < 1 and r["files"]["height"] > 900)
    bottom = call("splitAt", default, "timers", "notes", "bottom")
    r = rects_of(bottom)
    bottom_ok = (call("validate", bottom) and r["notes"]["y"] > r["timers"]["y"]
                 and r["notes"]["y"] < r["reminders"]["y"]
                 and abs(r["notes"]["x"] - r["timers"]["x"]) < 1)
    check("layout.js: splitAt left (nests in a row) and top (nests in a column)", left_ok and top_ok)
    check("layout.js: splitAt right and bottom, where the parent already runs that way, insert a sibling",
          right_ok and bottom_ok
          and right["sizes"] == [0.3, 0.2, 0.2, 0.3] and len(right["children"]) == 4
          and bottom["children"][1]["sizes"] == [0.25, 0.25, 0.5]
          and [c["panel"] for c in bottom["children"][1]["children"]] == ["timers", "notes", "reminders"])
    check("layout.js: splitAt onto itself or with a bad side is refused",
          call("splitAt", default, "notes", "notes", "left") is None
          and call("splitAt", default, "files", "notes", "middle") is None)

    no_todo = call("remove", default, "todo")
    no_mid = call("remove", no_todo, "files")
    check("layout.js: remove collapses single-child containers (and merges same-direction ones)",
          no_todo["children"][1] == {"panel": "files"} and len(no_mid["children"]) == 2
          and no_mid["dir"] == "row" and abs(sum(no_mid["sizes"]) - 1) < 1e-9)

    order = list(PANELS)
    templates_ok = True
    for name in ("sketch", "focus", "rows", "columns"):
        slots = call("templateSlotRects", name, RECT, GAP)
        tree = call("applyTemplate", name, order)
        placed = rects_of(tree)
        templates_ok = (templates_ok and len(slots) == 5 and call("validate", tree)
                        and all(s["width"] > 0 and s["height"] > 0 for s in slots)
                        and all(close(placed[order[i]], slots[i], 0.01) for i in range(5)))
    check("layout.js: each template has 5 slots and applyTemplate puts panel k into slot k", templates_ok)

    rank = call("rankByArea", now)
    check("layout.js: rankByArea ranks the default layout Notes, Files, To-Do, Timers, Reminders",
          rank == ["notes", "files", "todo", "timers", "reminders"])
    shuffled = ["reminders", "notes", "files", "todo", "timers"]
    again = call("applyTemplate", "columns", shuffled)
    placed = rects_of(again)
    check("layout.js: applying a template keeps the ranking (rank k goes to slot k)",
          [k for k, _ in sorted(placed.items(), key=lambda kv: kv[1]["x"])] == shuffled
          and call("orderForSlot", rank, "reminders", 1)[0] == "reminders"
          and call("orderForSlot", rank, "reminders", 1)[1:] == ["notes", "files", "todo", "timers"])
    tie = call("rankByArea", rects_of(call("applyTemplate", "columns", shuffled)))
    check("layout.js: equal areas tie-break in the order Notes, To-Do, Files, Timers, Reminders",
          tie == PANELS)

    grown = call("resize", default, [], 0, -0.5, RECT, GAP, MIN_W, MIN_H)
    r = rects_of(grown)
    shrunk = call("resize", default, [], 0, 0.9, RECT, GAP, MIN_W, MIN_H)
    r2 = rects_of(shrunk)
    inner = call("resize", default, [1], 0, -0.9, RECT, GAP, MIN_W, MIN_H)
    r3 = rects_of(inner)
    check("layout.js: resize clamps at the minimum size (both directions, nested containers too)",
          abs(r["notes"]["width"] - MIN_W) < 0.5 and abs(r2["todo"]["width"] - MIN_W) < 0.5
          and abs(r3["todo"]["height"] - MIN_H) < 0.5 and r3["files"]["height"] > MIN_H
          and abs(sum(grown["sizes"]) - 1) < 1e-9)

    columns = call("applyTemplate", "columns", order)
    check("layout.js: fits is false for five columns at 900px wide, true at 1632px",
          call("fits", columns, {"x": 0, "y": 0, "width": 900, "height": 600}, GAP, MIN_W, MIN_H) is False
          and call("fits", columns, RECT, GAP, MIN_W, MIN_H) is True)

    dup = {"dir": "row", "sizes": [0.5, 0.5], "children": [{"panel": "notes"}, {"panel": "notes"}]}
    custom = call("swap", default, "files", "todo")
    check("layout.js: invalid or unreadable saved JSON falls back to the default tree",
          call("parse", "{not json") == default and call("parse", "") == default
          and call("parse", json.dumps(dup)) == default and call("parse", json.dumps({"dir": "row"})) == default
          and call("parse", json.dumps(custom)) == custom)


def ui_steps(controller, settings_path: Path) -> list:  # noqa: ANN001
    """Real mouse and key events for the drag-and-drop, picker, splitter and persistence checks."""
    overlay, full = controller.overlay, controller.full
    left = Qt.MouseButton.LeftButton
    no_mod = Qt.KeyboardModifier.NoModifier
    memo: dict = {}
    tag = "layout"

    def js(value):  # noqa: ANN001, ANN202
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

    def saved_layout() -> dict:
        """The layout as a restarted app would read it from the settings file."""
        value = QSettings(str(settings_path), QSettings.Format.IniFormat).value("overlay/layoutJson", "")
        if isinstance(value, list):  # an unquoted INI value with commas comes back as a list
            value = ",".join(value)
        return json.loads(str(value)) if value else {}

    def reload_from_file() -> bool:
        before = layout_text()
        value = QSettings(str(settings_path), QSettings.Format.IniFormat).value("overlay/layoutJson", "")
        if isinstance(value, list):
            value = ",".join(value)
        overlay.loadLayout(str(value))
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
