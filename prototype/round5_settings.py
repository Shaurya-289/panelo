"""Self-test steps for round 5: the Settings page (see round5_tests.py)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from round5_tests import NO_MOD, Driver, Step, js

CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier


def settings_steps(controller) -> list[Step]:  # noqa: ANN001
    d = Driver(controller)
    full, store, overlay, library = d.full, d.store, controller.overlay, controller.library
    stub = controller.hotkeys.manager
    sounds = controller.sounds
    memo: dict = {}
    tag = "settings"
    tmp_dirs: list[Path] = []

    def ini(key: str):  # noqa: ANN202
        """A fresh read of the settings file, as a restarted app would see it."""
        value = QSettings(str(controller.settings_path), QSettings.Format.IniFormat).value(f"app/{key}")
        return str(value).lower() if isinstance(value, bool) else value

    def prop(name: str):  # noqa: ANN202
        return store.property(name)

    def press_combo(key, modifiers) -> None:  # noqa: ANN001
        QTest.keyClick(full, key, modifiers)

    def slider(name: str, fraction: float) -> QPoint:
        value = js(full.settingsSlider(name, fraction))
        return d.at(value["x"], value["y"])

    def drag_slider(name: str, start: float, end: float) -> None:
        a, b = slider(name, start), slider(name, end)
        d.press(a)
        d.move(b)
        d.release(b)

    def click_segment(key: str) -> None:
        value = js(full.settingsSegment(key))
        d.click_at(d.at(value["x"], value["y"]))

    def fire() -> None:
        memo["log_len"] = len(controller.notify_log)
        memo["sound_calls"] = len(sounds.calls)
        store.fireDemoReminder()

    def new_log() -> list:
        return controller.notify_log[memo["log_len"]:]

    def make_dir(name: str, files: bool = False, create: bool = True) -> Path:
        base = Path(tempfile.mkdtemp(prefix="proto-lib-move-"))
        tmp_dirs.append(base)
        target = base / name
        if create:
            target.mkdir()
            if files:
                (target / "keep.txt").write_text("hi", encoding="utf-8")
        return target

    def same(a: str, b: Path) -> bool:
        return Path(a).resolve() == b.resolve()

    def cleanup() -> None:
        for path in tmp_dirs:
            shutil.rmtree(path, ignore_errors=True)

    def click_outside_menu() -> None:
        # a spot over the General card, away from any open menu
        p = d.probe("settings.autoSwitch")
        d.click_at(d.at(p.x() - 400, p.y()))

    return [
        (0, lambda: controller.show_full("settings"), None),
        (600, lambda: full.setProperty("section", "settings"), None),
        # ── toggles persist ──
        (0, lambda: d.click("settings.startSwitch"), None),
        (0, lambda: d.click("settings.closeSwitch"), None),
        (0, lambda: d.click("settings.autoSwitch"), None),
        (900, None, (f"{tag}: the toggles apply live and persist (re-read from the settings file): start with Windows on, close-to-tray off, auto backup off",
                     lambda: prop("startWithWindows") is True and prop("closeHidesToTray") is False and prop("autoBackup") is False
                     and str(ini("startWithWindows")) == "true" and str(ini("closeHidesToTray")) == "false"
                     and str(ini("autoBackup")) == "false")),
        (0, lambda: d.click("settings.startSwitch"), None),
        (0, lambda: d.click("settings.closeSwitch"), None),
        (0, lambda: d.click("settings.autoSwitch"), None),
        (900, None, (f"{tag}: ...and toggling back persists too",
                     lambda: str(ini("startWithWindows")) == "false" and str(ini("closeHidesToTray")) == "true" and str(ini("autoBackup")) == "true")),
        # ── hotkey recording ──
        (0, lambda: memo.update(calls=len(stub.calls)), None),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, None, (f"{tag}: clicking the hotkey box starts recording ('Press keys…', highlighted)",
                     lambda: full.property("hotkeyRecording") is True)),
        (0, lambda: press_combo(Qt.Key.Key_K, CTRL), None),
        (200, None, (f"{tag}: an invalid combination (one modifier) shows the error and does not touch the hotkey",
                     lambda: "two modifiers" in str(full.property("hotkeyStatus")) and full.property("hotkeyRecording") is False
                     and len(stub.calls) == memo["calls"] and prop("hotkey") == "Ctrl+Alt+Space")),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, lambda: press_combo(Qt.Key.Key_K, CTRL | ALT), None),
        (200, None, (f"{tag}: a valid combination calls the (stubbed) HotkeyManager.replace, shows '✓ available' and is saved",
                     lambda: stub.calls[memo["calls"]:] == ["Ctrl+Alt+K"] and prop("hotkey") == "Ctrl+Alt+K"
                     and "available" in str(full.property("hotkeyStatus")) and full.property("hotkeyRecording") is False)),
        (0, lambda: stub.taken.add("Ctrl+Alt+F"), None),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, lambda: press_combo(Qt.Key.Key_F, CTRL | ALT), None),
        (200, None, (f"{tag}: a combination another app already uses shows 'already used by another app' and keeps the old hotkey",
                     lambda: "already used by another app" in str(full.property("hotkeyStatus")) and prop("hotkey") == "Ctrl+Alt+K")),
        (0, lambda: memo.update(calls=len(stub.calls)), None),
        (0, lambda: d.click("settings.hotkeyBox"), None),
        (200, lambda: QTest.keyClick(full, Qt.Key.Key_Escape, NO_MOD), None),
        (200, None, (f"{tag}: Esc cancels the recording and changes nothing",
                     lambda: full.property("hotkeyRecording") is False and len(stub.calls) == memo["calls"] and prop("hotkey") == "Ctrl+Alt+K")),
        (0, lambda: store.setProperty("hotkey", "Ctrl+Alt+Space"), None),
        # ── focus lengths: only the Focus page's ⚙ edits them now (the Settings section is gone) ──
        (0, lambda: memo.update(focus=round(int(prop("focusMs")) / 60000)), None),
        (0, lambda: full.setProperty("section", "focus"), None),
        (400, lambda: d.click("focus.gear"), None),
        (300, lambda: d.click("focus.focusPlus"), None),
        (900, None, (f"{tag}: the Focus page's ⚙ stepper still changes the focus length and persists it (re-read from the settings file)",
                     lambda: round(int(prop("focusMs")) / 60000) == memo["focus"] + 1
                     and str(full.property("lengthsText")).split(",")[0] == str(memo["focus"] + 1)
                     and str(ini("focusMs")) == str((memo["focus"] + 1) * 60000))),
        (0, lambda: d.key(Qt.Key.Key_Escape), None),
        (300, lambda: store.setProperty("focusMs", memo["focus"] * 60000), None),
        (0, lambda: full.setProperty("section", "settings"), None),
        (300, None, (f"{tag}: the Settings page has no focus length steppers any more",
                     lambda: d.probe("settings.focusPlus") is None and d.probe("settings.shortPlus") is None
                     and d.probe("settings.longPlus") is None and full.property("settingsLengthsText") is None)),
        # ── notifications ──
        (0, lambda: click_segment("windows"), None),
        (200, fire, None),
        (300, None, (f"{tag}: style 'Windows notification' sends only the Windows notification (no glass toast)",
                     lambda: prop("notificationStyle") == "windows" and [kind for kind, _ in new_log()] == ["windows"])),
        (0, lambda: click_segment("both"), None),
        (200, fire, None),
        (300, None, (f"{tag}: style 'Both' sends the toast and the Windows notification",
                     lambda: prop("notificationStyle") == "both" and sorted(kind for kind, _ in new_log()) == ["toast", "windows"])),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: click_segment("toast"), None),
        (200, fire, None),
        (300, None, (f"{tag}: style 'Glass toast' shows only the toast, and with the sound on None it plays nothing",
                     lambda: prop("notificationStyle") == "toast" and [kind for kind, _ in new_log()] == ["toast"]
                     and sounds.calls[memo["sound_calls"]:] == [])),
        (0, lambda: controller.toast.dismiss(), None),
        # ── notification sound picker (the player is a stub: it only records what would play) ──
        (0, lambda: memo.update(options=[o for o in sounds.options() if o["path"]]), None),
        (0, lambda: None, (f"{tag}: the sound list is 'None' plus only Windows sounds whose file exists",
                           lambda: sounds.options()[0] == {"path": "", "label": "None"} and len(memo["options"]) >= 1
                           and all(Path(o["path"]).is_file() and Path(o["path"]).parent == sounds.media_dir for o in memo["options"])
                           and [o["label"] for o in memo["options"]] == [o["label"] for o in memo["options"] if o["label"] in
                                {"Notify", "Ding", "Chimes", "Chord", "Balloon", "Alarm", "Ring"}])),
        (0, lambda: d.click("settings.soundButton"), None),
        (300, None, (f"{tag}: the sound button opens the list", lambda: full.property("soundMenuOpen") is True)),
        (0, lambda: memo.update(calls=len(sounds.calls)), None),
        (0, lambda: d.move(d.probe("settings.menuItem", 1)), None),
        (700, None, (f"{tag}: hovering a sound row for a moment plays a preview of it (stub called with that file, nothing chosen yet)",
                     lambda: sounds.calls[memo["calls"]:] == [("preview", memo["options"][0]["path"])] and prop("notificationSound") == "")),
        (0, lambda: memo.update(calls=len(sounds.calls), stops=sounds.stops), None),
        (0, lambda: d.move(d.probe("settings.menuItem", 0)), None),
        (700, None, (f"{tag}: hovering 'None' stops the preview and plays nothing new",
                     lambda: len(sounds.calls) == memo["calls"] and sounds.stops > memo["stops"])),
        (0, lambda: memo.update(calls=len(sounds.calls), pick=min(1, len(memo["options"]) - 1)), None),
        (0, lambda: d.click("settings.menuItem", memo["pick"] + 1), None),
        (900, None, (f"{tag}: choosing a sound plays its preview (stop first), saves it and closes the list",
                     lambda: prop("notificationSound") == memo["options"][memo["pick"]]["path"]
                     and sounds.calls[memo["calls"]:] == [("preview", prop("notificationSound"))]
                     and full.property("soundMenuOpen") is False
                     and Path(str(ini("notificationSound"))).resolve() == Path(prop("notificationSound")).resolve())),
        (0, lambda: memo.update(chosen=str(prop("notificationSound"))), None),
        (0, fire, None),
        (300, None, (f"{tag}: a glass toast plays the selected sound",
                     lambda: [kind for kind, _ in new_log()] == ["toast"] and sounds.calls[memo["sound_calls"]:] == [("play", memo["chosen"])])),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: (controller.on_focus_completed("Focus session done"), controller.on_timer_finished("Zed")), None),
        (300, None, (f"{tag}: ...a focus phase end and a finished timer play it too",
                     lambda: sounds.calls[memo["sound_calls"] + 1:] == [("play", memo["chosen"])] * 2)),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: d.click("settings.soundButton"), None),
        (300, lambda: d.click("settings.menuItem", 0), None),
        (900, None, (f"{tag}: choosing 'None' saves '' and closes the list",
                     lambda: prop("notificationSound") == "" and str(ini("notificationSound") or "") == ""
                     and full.property("soundMenuOpen") is False)),
        (0, fire, None),
        (300, None, (f"{tag}: with 'None' a toast shows silently (no sound call)",
                     lambda: [kind for kind, _ in new_log()] == ["toast"] and sounds.calls[memo["sound_calls"]:] == [])),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: d.click("settings.alertsSwitch"), None),
        (200, fire, None),
        (300, None, (f"{tag}: with reminder alerts off a due reminder shows no alert at all",
                     lambda: prop("reminderAlerts") is False and new_log() == [])),
        (0, lambda: d.click("settings.alertsSwitch"), None),
        (200, None, (f"{tag}: ...and the notification settings persist",
                     lambda: prop("reminderAlerts") is True and str(ini("notificationStyle")) == "toast")),
        # ── appearance sliders ──
        (0, lambda: drag_slider("blur", 0.95, 0.5), None),
        (0, lambda: drag_slider("dim", 0.4, 0.2), None),
        (300, None, (f"{tag}: the blur and dim sliders change BlurredBackdrop in the Full App and in both overlay layers",
                     lambda: abs(float(prop("backdropBlur")) - 0.5) < 0.03 and abs(float(prop("backdropDim")) - 0.2) < 0.03
                     and abs(float(full.property("backdropBlurValue")) - float(prop("backdropBlur"))) < 1e-6
                     and abs(float(full.property("backdropDimValue")) - float(prop("backdropDim"))) < 1e-6
                     and abs(float(overlay.property("backdropBlurValue")) - float(prop("backdropBlur"))) < 1e-6
                     and abs(float(overlay.property("backdropDimValue")) - float(prop("backdropDim"))) < 1e-6
                     and abs(float(overlay.property("ghostBlurValue")) - float(prop("backdropBlur"))) < 1e-6
                     and abs(float(overlay.property("ghostDimValue")) - float(prop("backdropDim"))) < 1e-6)),
        (800, None, (f"{tag}: ...and the appearance values persist in the settings file",
                     lambda: abs(float(ini("backdropBlur")) - float(prop("backdropBlur"))) < 1e-6 and abs(float(ini("backdropDim")) - float(prop("backdropDim"))) < 1e-6)),
        (0, lambda: (store.setProperty("backdropBlur", 1.0), store.setProperty("backdropDim", 0.45)), None),
        (0, lambda: drag_slider("size", 0.6, 0.0), None),
        (300, None, (f"{tag}: the overlay size slider (60%) changes the overlay's group rectangle",
                     lambda: abs(float(prop("overlaySize")) - 0.6) < 0.001
                     and abs(float(overlay.property("groupW")) - 0.6 * float(overlay.property("width"))) < 1.0
                     and abs(float(overlay.property("groupH")) - 0.6 * float(overlay.property("height"))) < 1.0
                     and abs(float(overlay.property("baseRect").width()) - float(overlay.property("groupW"))) < 1.0)),
        (0, lambda: drag_slider("size", 0.0, 1.0), None),
        (300, None, (f"{tag}: the size slider's maximum is 90% (not 95%), and dragging to the end gives 90%, a bigger group again",
                     lambda: abs(float(full.property("settingsSizeMax")) - 0.90) < 1e-9
                     and abs(float(prop("overlaySize")) - 0.90) < 0.001
                     and abs(float(overlay.property("groupW")) - 0.90 * float(overlay.property("width"))) < 1.0)),
        (0, lambda: store.setProperty("overlaySize", 0.95), None),
        (900, None, (f"{tag}: a stored size above the maximum (0.95) is clamped down to 0.90 (the same handler runs when it is loaded) and 0.90 is what is saved",
                     lambda: abs(float(prop("overlaySize")) - 0.90) < 1e-9 and abs(float(ini("overlaySize")) - 0.90) < 1e-6)),
        # five columns need ~1140 px: dragging the size down must clamp, not squeeze the panels
        (0, lambda: overlay.applyTemplateNamed("columns"), None),
        (200, lambda: drag_slider("size", 1.0, 0.0), None),
        # (a screen wide enough for five columns at 60% needs no clamp; one too narrow for them even at 90% keeps 90%)
        (300, None, (f"{tag}: the layout minimum is kept: a small size is clamped up (never past 90%) so no panel drops below 220x160",
                     lambda: 0.6 - 1e-9 <= float(overlay.property("sizeFraction")) <= 0.90 + 1e-9
                     and (float(overlay.property("sizeFraction")) > 0.6 + 1e-6
                          or 0.6 * float(overlay.property("width")) >= 5 * 220 + 4 * float(overlay.property("gap")))
                     and (float(overlay.property("sizeFraction")) >= 0.90 - 1e-9
                          or all(r["width"] >= 219.5 and r["height"] >= 159.5 for r in js(overlay.property("rects")).values())))),
        (0, lambda: (overlay.resetLayout(), store.setProperty("overlaySize", 0.85)), None),
        # ── files: the library location ──
        (0, lambda: d.click("settings.openFolder"), None),
        (200, None, (f"{tag}: 'Open folder' opens the library folder in Explorer (stubbed)",
                     lambda: library.launched[-1] == ("openFolder", ""))),
        (0, lambda: memo.update(orig=Path(library.rootPath).resolve(), new=make_dir("newlib", create=False)), None),
        (0, lambda: full.requestLibraryMove(str(memo["new"])), None),
        (400, None, (f"{tag}: Change… to a new folder asks 'Move your files to ...?'",
                     lambda: full.property("confirmOpen") is True and library.root == memo["orig"])),
        (0, lambda: d.click("confirmYes"), None),
        (600, None, (f"{tag}: confirming moves the library contents and switches the root",
                     lambda: same(library.rootPath, memo["new"]) and (memo["new"] / "Study/Physics").is_dir()
                     and not any(memo["orig"].iterdir()) and same(str(prop("libraryPath")), memo["new"]))),
        (0, lambda: memo.update(full_dir=make_dir("busy", files=True)), None),
        (0, lambda: full.requestLibraryMove(str(memo["full_dir"])), None),
        (300, None, (f"{tag}: a folder that already has files is refused (no dialog, an error shows)",
                     lambda: full.property("confirmOpen") is False and "empty or new" in str(full.property("filesError"))
                     and same(library.rootPath, memo["new"]))),
        (0, lambda: full.requestLibraryMove(str(memo["orig"])), None),
        (400, lambda: d.click("confirmYes"), None),
        (600, None, (f"{tag}: moving back to the (now empty) original folder works",
                     lambda: same(library.rootPath, memo["orig"]) and (memo["orig"] / "Study/Physics").is_dir()
                     and full.property("filesError") == "")),
        # ── backups ──
        (0, lambda: memo.update(backups=int(full.property("backupCount"))), None),
        (0, lambda: d.click("settings.backUpNow"), None),
        (300, None, (f"{tag}: Back up now adds a backup entry at the top",
                     lambda: int(full.property("backupCount")) == min(memo["backups"] + 1, int(prop("keepBackups")))
                     and js(prop("backups"))[0]["at"] >= js(prop("backups"))[1]["at"])),
        (0, lambda: d.click("settings.keepButton"), None),
        (300, None, (f"{tag}: the 'Keep the last' menu opens", lambda: full.property("settingsMenuOpen") is True)),
        (0, click_outside_menu, None),
        (300, None, (f"{tag}: click-through: clicking outside the menu closes it and does not flip the toggle under the click",
                     lambda: full.property("settingsMenuOpen") is False and prop("autoBackup") is True)),
        (0, lambda: d.click("settings.keepButton"), None),
        (300, lambda: d.click("settings.menuItem", 0), None),
        (300, None, (f"{tag}: choosing '3 backups' sets how many are kept", lambda: prop("keepBackups") == 3)),
        (0, lambda: d.click("settings.backUpNow"), None),
        (300, None, (f"{tag}: Back up now trims the list to the number kept", lambda: int(full.property("backupCount")) == 3)),
        (0, lambda: memo.update(log_len=len(controller.notify_log)), None),
        (0, lambda: d.click("settings.restore", 0), None),
        (400, None, (f"{tag}: Restore asks for confirmation", lambda: full.property("confirmOpen") is True)),
        (0, lambda: d.click("confirmYes"), None),
        (400, None, (f"{tag}: confirming Restore shows the (fake) success toast",
                     lambda: any("restored" in text for _, text in controller.notify_log[memo["log_len"]:]))),
        (0, lambda: controller.toast.dismiss(), None),
        (0, lambda: memo.update(backups=int(full.property("backupCount"))), None),
        (0, lambda: d.click("settings.backupMenu", 0), None),
        (300, lambda: d.click("settings.menuItem", 0), None),
        (300, None, (f"{tag}: a backup's ⋯ menu > Delete removes it", lambda: int(full.property("backupCount")) == memo["backups"] - 1)),
        (0, lambda: store.setProperty("keepBackups", 7), None),
        (0, cleanup, None),
        (0, controller.hide_full, None),
    ]
