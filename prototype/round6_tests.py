"""Self-test steps for round 6: the focus Reset / Start over, the fullscreen focus view and the
overlay size limit (the sound picker checks live with the other Settings checks in
round5_settings.py). Used by `main.py --selftest`; real mouse and key events (QTest), a stubbed
sound player, and nothing outside temp folders is touched."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from app import FullWindowState
from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QUrl
from PySide6.QtGui import QWindow
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QTest
from round5_tests import Driver, Step, js

QML_DIR = Path(__file__).resolve().parent / "qml"
SEEN = QWindow.Visibility


def stored_size_loads(stored: float) -> float:
    """What a fresh Store (a second QML engine, reading a settings file that holds `stored`) makes
    of the saved overlay size."""
    tmp = Path(tempfile.mkdtemp(prefix="proto-size-"))
    try:
        ini = tmp / "settings.ini"
        settings = QSettings(str(ini), QSettings.Format.IniFormat)
        settings.setValue("app/overlaySize", stored)
        settings.sync()
        del settings
        engine = QQmlEngine()
        engine.addImportPath(str(QML_DIR))
        engine.rootContext().setContextProperty("SettingsLocation", QUrl.fromLocalFile(str(ini)))
        component = QQmlComponent(engine)
        component.setData(b"import QtQuick\nimport Proto\nQtObject { property real v: Store.overlaySize }", QUrl())
        obj = component.create()
        value = float(obj.property("v")) if obj is not None else -1.0
        del obj, component
        engine.deleteLater()
        return value
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def round6_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, store = d.full, d.store
    memo: dict = {}
    tag = "round 6"

    def prop(name: str):  # noqa: ANN202
        return store.property(name)

    def minutes() -> int:
        return round(int(prop("focusMs")) / 60000)

    def screen_rect() -> QRect:
        return QRect(full.screen().geometry())

    def available() -> QRect:
        return QRect(full.screen().availableGeometry())

    def geometry() -> QRect:
        return QRect(full.geometry())

    def disk():  # noqa: ANN202
        return FullWindowState(QSettings(str(controller.settings_path), QSettings.Format.IniFormat))

    def is_fullscreen() -> bool:
        return full.visibility() == SEEN.FullScreen and full.property("zenActive") is True and geometry() == screen_rect()

    def wiggle() -> None:
        """A real mouse move, a few pixels off from the last one each time."""
        memo["n"] = memo.get("n", 0) + 1
        QTest.mouseMove(full, QPoint(300 + 7 * memo["n"], 300 + 5 * memo["n"]))

    def timer_count() -> int:
        return len(js(prop("timers")))

    return [
        # ── Focus page: Reset and Start over ──
        (0, lambda: controller.show_full("focus"), None),
        (600, lambda: (store.focusSetPhase(2), store.setProperty("focusRemaining", 400), store.setProperty("focusRunning", True)), None),
        (0, lambda: memo.update(total=int(prop("focusTotal"))), None),
        (0, lambda: d.move(d.probe("focus.reset")), None),
        (800, None, (f"{tag}: hovering the Reset button shows the 'Reset session' hint", lambda: full.property("resetHintShown") is True)),
        (0, lambda: d.move(d.at(40, 600)), None),
        (200, None, (f"{tag}: ...and moving away hides it", lambda: full.property("resetHintShown") is False)),
        (0, lambda: d.click("focus.reset"), None),
        (300, None, (f"{tag}: the Reset button pauses and restores the full length of the current session (phase kept)",
                     lambda: prop("focusRunning") is False and int(prop("focusRemaining")) == memo["total"] and int(prop("focusPhase")) == 2)),
        (0, lambda: (store.setProperty("focusRemaining", 400), store.setProperty("focusRunning", True)), None),
        (0, lambda: d.click("focus.gear"), None),
        (300, None, (f"{tag}: the gear popover offers 'Start over from Session 1'", lambda: full.property("lengthsOpen") is True and d.probe("focus.startOver") is not None)),
        (0, lambda: d.click("focus.startOver"), None),
        (300, None, (f"{tag}: Start over pauses, goes to phase 0 'Session 1 of 4' with the full focus length, and closes the popover",
                     lambda: prop("focusRunning") is False and int(prop("focusPhase")) == 0 and prop("focusLabel") == "Session 1 of 4"
                     and int(prop("focusRemaining")) == int(prop("focusTotal")) == int(store.focusDuration(0))
                     and full.property("lengthsOpen") is False)),
        # ── Fullscreen: a click on a control does not leak, calm mode, exit ways ──
        (0, lambda: full.setProperty("zenCalmDelay", 1200), None),
        (0, lambda: memo.update(normal=geometry()), None),
        (0, lambda: d.click("focus.fullscreen"), None),
        (700, None, (f"{tag}: the ⛶ button makes the window fullscreen and shows the zen view",
                     lambda: is_fullscreen() and full.property("zenVisible") is True and controller.full_is_fullscreen()
                     and re.fullmatch(r"\d\d:\d\d", str(full.property("zenTime"))))),
        (0, None, (f"{tag}: the fullscreen geometry is not saved as the window's normal geometry",
                   lambda: disk().geometry == memo["normal"] and not disk().maximized)),
        (0, None, (f"{tag}: the zen view starts with its controls shown", lambda: full.property("zenControlsShown") is True)),
        (1800, None, (f"{tag}: calm mode: 1.2 s without mouse movement hides the controls (and the cursor)",
                     lambda: full.property("zenControlsShown") is False)),
        (0, wiggle, None),
        (150, None, (f"{tag}: a mouse move shows the controls again", lambda: full.property("zenControlsShown") is True)),
        (1800, None, (f"{tag}: ...and calm mode comes back after the next quiet spell", lambda: full.property("zenControlsShown") is False)),
        (0, lambda: d.move(d.probe("zen.reset")), None),
        (150, None, (f"{tag}: moving onto the (hidden) controls' own spot wakes them too", lambda: full.property("zenControlsShown") is True)),
        (800, None, (f"{tag}: hovering a zen control (reset) still shows its hint under the wake-up layer",
                     lambda: full.property("zenResetHintShown") is True and full.property("zenControlsShown") is True)),
        (0, wiggle, None),
        (0, lambda: memo.update(timers=timer_count(), running=prop("focusRunning")), None),
        (0, lambda: d.click("focus.preset", 0), None),
        (200, None, (f"{tag}: click-through: a click in fullscreen over the page's preset chip starts no timer",
                     lambda: timer_count() == memo["timers"] and is_fullscreen())),
        (0, lambda: d.click("zen.play"), None),
        (200, None, (f"{tag}: the zen play button toggles the timer once (no second click leaks through)", lambda: prop("focusRunning") is True)),
        (0, lambda: d.click("zen.play"), None),
        (200, None, (f"{tag}: ...and pauses it again", lambda: prop("focusRunning") is False)),
        (0, lambda: store.setProperty("focusRemaining", 300), None),
        (0, lambda: d.click("zen.reset"), None),
        (200, None, (f"{tag}: the zen reset button restores the session length", lambda: int(prop("focusRemaining")) == int(prop("focusTotal")))),
        (0, lambda: d.click("zen.skip"), None),
        (200, None, (f"{tag}: the zen skip button goes to the next phase", lambda: int(prop("focusPhase")) == 1)),
        (0, lambda: (store.focusSetPhase(0), store.setProperty("focusRemaining", 1), store.setProperty("focusRunning", True), memo.update(toasts=controller.focus_toasts, log=len(controller.notify_log))), None),
        (1700, None, (f"{tag}: a phase end while fullscreen still fires the toast",
                      lambda: controller.focus_toasts == memo["toasts"] + 1 and ("toast", controller.last_focus_message) in controller.notify_log[memo["log"]:]
                      and is_fullscreen())),
        (0, lambda: controller.toast.dismiss(), None),
        (0, wiggle, None),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (900, None, (f"{tag}: Esc leaves fullscreen and restores the previous normal window (same geometry)",
                     lambda: full.visibility() == SEEN.Windowed and full.property("zenActive") is False and geometry() == memo["normal"]
                     and full.property("zenVisible") is False and not controller.full_is_fullscreen())),
        (0, lambda: d.click("focus.fullscreen"), None),
        (900, None, (f"{tag}: entering fullscreen again works", lambda: is_fullscreen())),
        (0, wiggle, None),
        (0, lambda: d.click("zen.exit"), None),
        (900, None, (f"{tag}: the exit button leaves fullscreen (normal geometry back)",
                     lambda: full.visibility() == SEEN.Windowed and geometry() == memo["normal"] and full.property("zenActive") is False)),
        (0, lambda: d.click("focus.fullscreen"), None),
        (900, lambda: wiggle(), None),
        (0, lambda: QTest.mouseDClick(full, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, d.probe("zen.time")), None),
        (900, None, (f"{tag}: a double-click on the time leaves fullscreen",
                     lambda: full.visibility() == SEEN.Windowed and geometry() == memo["normal"] and full.property("zenActive") is False)),
        # maximized before: Esc returns to maximized, and the saved normal geometry is untouched
        (0, full.showMaximized, None),
        (700, lambda: d.click("focus.fullscreen"), None),
        (900, None, (f"{tag}: a maximized window can go fullscreen too", lambda: is_fullscreen())),
        (0, None, (f"{tag}: ...and the settings still say maximized with the old normal geometry",
                   lambda: disk().maximized and disk().geometry == memo["normal"])),
        (0, wiggle, None),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (900, None, (f"{tag}: Esc from fullscreen returns to the maximized state (available area, not over the taskbar)",
                     lambda: full.visibility() == SEEN.Maximized and geometry() == available() and full.property("zenActive") is False)),
        # the hotkey in fullscreen: exits fullscreen and hides to the tray
        (0, lambda: d.click("focus.fullscreen"), None),
        (900, None, (f"{tag}: fullscreen again (from maximized)", lambda: is_fullscreen())),
        (0, controller.on_hotkey, None),
        (500, None, (f"{tag}: the hotkey in fullscreen leaves the zen view and hides the app to the tray (the overlay stays closed)",
                     lambda: not controller.full_visible() and not controller.overlay_visible() and full.property("zenActive") is False
                     and not controller.full_is_fullscreen())),
        (0, controller.open_full_from_tray, None),
        (900, None, (f"{tag}: showing the Full App again gives the maximized window, not a fullscreen one",
                     lambda: full.visibility() == SEEN.Maximized and geometry() == available() and full.property("zenActive") is False)),
        (0, full.showNormal, None),
        (800, None, (f"{tag}: the window is back to normal afterwards (saved state restored)",
                     lambda: full.visibility() == SEEN.Windowed and not disk().maximized)),
        (0, lambda: full.setProperty("zenCalmDelay", 3000), None),
        (0, controller.hide_full, None),
        # ── overlay size ──
        (0, lambda: None, (f"{tag}: a saved overlay size of 0.95 loads as 0.90 in a fresh Store; 0.7 stays",
                           lambda: abs(stored_size_loads(0.95) - 0.90) < 1e-9 and abs(stored_size_loads(0.7) - 0.7) < 1e-9)),
    ]

