import QtQuick
import QtQuick.Dialogs
import "dates.js" as Dates

// The Settings view: one scrolling page with sections (General, Hotkey, Focus, Notifications,
// Appearance, Files, Backups). Changes apply live and persist (see Store). Hotkey recording is
// validated and registered by the Python side (`Hotkeys`).
Item {
    id: page
    property bool recording: false
    property string heldText: ""               // modifiers held while recording
    property string hotkeyStatus: ""
    property bool hotkeyOk: false
    property string filesError: ""
    property string usageText: ""
    property var menuBackupId: ""
    readonly property bool menuOpen: keepMenu.isOpen || backupMenu.isOpen || soundMenu.isOpen
    // [{ path, label }]: "None" first, then the Windows sounds that exist on this PC
    readonly property var soundOptions: Sounds.soundOptions()
    readonly property string soundLabel: {
        const found = soundOptions.find(o => o.path === Store.notificationSound)
        return found ? found.label : "None"
    }
    readonly property real listW: Math.min(page.width - 48, 760)
    signal ask(string title, string message, string confirmLabel, var callback)

    function handleEscape() {
        if (recording) {
            stopRecording()
            return true
        }
        return soundMenu.handleEscape() || keepMenu.handleEscape() || backupMenu.handleEscape()
    }

    // ── Hotkey recording ────────────────────────────────────────────
    function startRecording() {
        hotkeyStatus = ""
        heldText = ""
        recording = true
        capture.forceActiveFocus()
    }
    function stopRecording() {
        recording = false
        heldText = ""
        page.forceActiveFocus()
    }
    function modifierText(modifiers) {
        const mods = []
        if (modifiers & Qt.ControlModifier) mods.push("Ctrl")
        if (modifiers & Qt.AltModifier) mods.push("Alt")
        if (modifiers & Qt.ShiftModifier) mods.push("Shift")
        if (modifiers & Qt.MetaModifier) mods.push("Win")
        return mods
    }
    function comboText(event) {
        const mods = modifierText(event.modifiers)
        let key
        if (event.key >= Qt.Key_A && event.key <= Qt.Key_Z) key = String.fromCharCode(event.key)
        else if (event.key >= Qt.Key_0 && event.key <= Qt.Key_9) key = String.fromCharCode(event.key)
        else if (event.key === Qt.Key_Space) key = "Space"
        else if (event.key >= Qt.Key_F1 && event.key <= Qt.Key_F35) key = "F" + (event.key - Qt.Key_F1 + 1)
        else key = event.text !== "" ? event.text.toUpperCase() : "?"
        return mods.concat([key]).join("+")
    }
    function tryCombo(text) {
        const result = Hotkeys.tryHotkey(text)
        hotkeyOk = result.ok
        if (result.ok) {
            Store.hotkey = result.text
            hotkeyStatus = "✓ available"
        } else {
            hotkeyStatus = result.error
        }
        stopRecording()
    }

    // ── Files ───────────────────────────────────────────────────────
    function refreshUsage() { usageText = Library.usage() }
    Component.onCompleted: refreshUsage()
    Connections {
        target: Library
        function onChanged() { page.refreshUsage() }
        function onRootChanged() { page.refreshUsage() }
        // The move runs on a worker thread: the answer comes here.
        function onMoveFinished(error) {
            page.filesError = error
            if (error === "")
                Store.libraryPath = Library.rootPath
        }
    }
    // The folder dialog's result, or a path from the self-test: validate, confirm, then move.
    function requestMove(path) {
        filesError = Library.checkNewRoot(path)
        if (filesError !== "")
            return
        page.ask("Move your files to " + path + "?",
                 "Your library (" + usageText + ") moves to that folder and becomes the new library location.",
                 "Move files", () => { page.filesError = Library.moveTo(path) })
    }
    FolderDialog {
        id: folderDialog
        title: "Choose an empty or new folder"
        onAccepted: page.requestMove(selectedFolder.toString().replace(/^file:\/\/\//, "").replace(/\//g, "\\"))
    }

    // ── Used by the self-test and the demos ─────────────────────────
    function reveal(item) {
        const y = item.mapToItem(flick.contentItem, 0, 0).y
        flick.contentY = Math.max(0, Math.min(Math.max(0, flick.contentHeight - flick.height), y - 140))
    }
    function demoSoundPicker() {
        reveal(soundButton)
        soundMenu.openAt(soundButton, soundButton.width - soundMenu.cardWidth, soundButton.height + 4)
    }
    function demoAppearance() {
        Store.overlaySize = Store.overlaySizeMax
        reveal(blurSlider)
    }
    // The tour's "Change hotkey": scroll to the recorder and start listening for the new combination
    function revealHotkey() {
        reveal(hotkeyBox)
        startRecording()
    }
    function scrollTo(position) {
        flick.contentY = position === "bottom" ? Math.max(0, flick.contentHeight - flick.height) : 0
    }
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function probe(name, index) {
        const table = ({
            startSwitch: startSwitch, closeSwitch: closeSwitch, hotkeyBox: hotkeyBox,
            alertsSwitch: alertsSwitch, soundButton: soundButton, autoSwitch: autoSwitch,
            openFolder: openFolder, changeFolder: changeFolder, keepButton: keepButton, backUpNow: backUpNow,
            nameBox: nameBox, tourAgain: tourAgain
        })
        let item = table[name]
        if (!item && name === "restore") item = backupRepeater.itemAt(index).restoreButton
        if (!item && name === "backupMenu") item = backupRepeater.itemAt(index).moreButton
        if (!item && name === "menuItem") item = (soundMenu.isOpen ? soundMenu : keepMenu.isOpen ? keepMenu : backupMenu).itemAtIndex(index)
        if (item) {
            reveal(item)
            return center(item)
        }
        return null
    }
    function segmentPoint(key) {
        reveal(styleSeg)
        return styleSeg.center(key)
    }
    function sliderPoint(name, fraction) {
        const s = ({ blur: blurSlider, dim: dimSlider, size: sizeSlider })[name]
        reveal(s)
        const p = s.track.mapToItem(null, fraction * s.track.width, s.track.height / 2)
        return { x: p.x, y: p.y }
    }
    readonly property real sizeSliderMax: sizeSlider.to
    readonly property bool soundMenuOpen: soundMenu.isOpen
    readonly property int backupCount: Store.backups.length

    // ── Layout helpers ──────────────────────────────────────────────
    component SettingCard: GlassPanel {
        default property alias rows: column.data
        width: page.listW
        height: column.implicitHeight + 8
        Column {
            id: column
            x: Theme.pad; y: 4
            width: parent.width - Theme.pad * 2
        }
    }
    component SettingRow: Item {
        id: setting
        property string label: ""
        property string hint: ""
        property bool last: false
        default property alias control: slot.data
        width: parent.width
        height: Math.max(54, labelColumn.implicitHeight + 22)
        Column {
            id: labelColumn
            anchors { left: parent.left; right: slot.left; rightMargin: 16; verticalCenter: parent.verticalCenter }
            spacing: 2
            Text {
                width: parent.width
                text: setting.label
                elide: Text.ElideRight
                font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
                color: Theme.text
            }
            Text {
                visible: setting.hint !== ""
                width: parent.width
                text: setting.hint
                wrapMode: Text.Wrap
                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                color: Theme.textMuted
            }
        }
        Item {
            id: slot
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            width: childrenRect.width
            height: childrenRect.height
        }
        Rectangle {
            visible: !setting.last
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: 1; color: Qt.rgba(1, 1, 1, 0.06)
        }
    }

    Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight + 40
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: column
            x: (flick.width - width) / 2
            y: 8
            width: page.listW
            spacing: 8

            // ── GENERAL ─────────────────────────────────────────────
            SectionLabel { text: "General"; leftPadding: 4; topPadding: 6 }
            SettingCard {
                SettingRow {
                    label: "Your name"
                    hint: "The overlay greets you with it"
                    Rectangle {
                        width: 220; height: 34
                        radius: Theme.radius
                        color: Qt.rgba(1, 1, 1, 0.05)
                        border.width: 1
                        border.color: nameBox.input.activeFocus ? Theme.borderStrong : Theme.border
                        TextBox {
                            id: nameBox
                            anchors { left: parent.left; leftMargin: 12; right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                            placeholder: "Your name"
                            multiline: false
                            Component.onCompleted: text = Store.userName
                            // live: what is typed is saved at once (the tour and the greeting follow)
                            onTextChanged: if (input.activeFocus && Store.userName !== text.trim()) Store.userName = text.trim()
                            Connections {
                                target: Store
                                function onUserNameChanged() { if (!nameBox.input.activeFocus) nameBox.text = Store.userName }
                            }
                        }
                        MouseArea { anchors.fill: parent; z: -1; acceptedButtons: Qt.LeftButton; onClicked: nameBox.input.forceActiveFocus() }
                    }
                }
                SettingRow {
                    label: "Start with Windows"
                    hint: "Opens the app in the tray when you sign in to Windows"
                    Switch { id: startSwitch; checked: Store.startWithWindows; onToggled: Store.startWithWindows = !Store.startWithWindows }
                }
                SettingRow {
                    label: "Close button hides to tray"
                    hint: "Off: closing the window quits the app"
                    Switch { id: closeSwitch; checked: Store.closeHidesToTray; onToggled: Store.closeHidesToTray = !Store.closeHidesToTray }
                }
                SettingRow {
                    label: "Welcome tour"
                    hint: "The three-step introduction shown on the first launch"
                    last: true
                    TextButton { id: tourAgain; label: "Show the welcome tour again"; onClicked: Store.tourDone = false }
                }
            }

            // ── HOTKEY ──────────────────────────────────────────────
            SectionLabel { text: "Hotkey"; leftPadding: 4; topPadding: 14 }
            SettingCard {
                SettingRow {
                    label: "Open overlay"
                    hint: page.hotkeyStatus !== "" ? "" : "Click the box, then press the new combination"
                    last: true
                    Row {
                        spacing: 12
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: page.hotkeyStatus !== ""
                            text: page.hotkeyStatus
                            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                            color: page.hotkeyOk ? Qt.rgba(0.50, 0.82, 0.58, 1) : Qt.rgba(0.93, 0.42, 0.44, 1)
                        }
                        Rectangle {
                            id: hotkeyBox
                            width: 210; height: 34
                            radius: Theme.radius
                            color: page.recording ? Qt.rgba(1, 1, 1, 0.10) : Qt.rgba(1, 1, 1, 0.05)
                            border.width: page.recording ? 2 : 1
                            border.color: page.recording ? Theme.accent : Theme.border
                            Behavior on color { ColorAnimation { duration: Theme.fast } }
                            Text {
                                anchors.centerIn: parent
                                text: page.recording
                                      ? (page.heldText !== "" ? page.heldText + " + …" : "Press keys…")
                                      : Store.hotkey.replace(/\+/g, " + ")
                                font.family: Theme.font; font.pixelSize: Theme.sizeBody
                                font.weight: page.recording ? Font.Normal : Font.Medium
                                color: page.recording && page.heldText === "" ? Theme.textSecondary : Theme.text
                            }
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.LeftButton
                                onClicked: page.recording ? page.stopRecording() : page.startRecording()
                            }
                            // Takes the keys while recording. Esc cancels (the window routes it to handleEscape).
                            Item {
                                id: capture
                                activeFocusOnTab: false
                                onActiveFocusChanged: if (!activeFocus && page.recording) page.stopRecording()
                                Keys.onPressed: (event) => {
                                    if (!page.recording)
                                        return
                                    event.accepted = true
                                    if (event.key === Qt.Key_Escape && event.modifiers === Qt.NoModifier) {
                                        page.stopRecording()
                                        return
                                    }
                                    if ([Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr].indexOf(event.key) >= 0) {
                                        page.heldText = page.modifierText(event.modifiers).join(" + ")
                                        return
                                    }
                                    page.tryCombo(page.comboText(event))
                                }
                                Keys.onReleased: (event) => {
                                    if (page.recording)
                                        page.heldText = page.modifierText(event.modifiers).join(" + ")
                                }
                            }
                        }
                    }
                }
            }

            // ── NOTIFICATIONS ───────────────────────────────────────
            SectionLabel { text: "Notifications"; leftPadding: 4; topPadding: 14 }
            SettingCard {
                SettingRow {
                    label: "Reminder alerts"
                    hint: "Show an alert when a reminder comes due"
                    Switch { id: alertsSwitch; checked: Store.reminderAlerts; onToggled: Store.reminderAlerts = !Store.reminderAlerts }
                }
                SettingRow {
                    label: "Style"
                    Segmented {
                        id: styleSeg
                        current: Store.notificationStyle
                        options: [{ key: "toast", label: "Glass toast" }, { key: "windows", label: "Windows notification" }, { key: "both", label: "Both" }]
                        onPicked: (key) => Store.notificationStyle = key
                    }
                }
                SettingRow {
                    label: "Notification sound"
                    hint: "Plays when a glass toast appears. Hover or click a sound to hear it"
                    last: true
                    Rectangle {
                        id: soundButton
                        width: 190; height: 34
                        radius: Theme.radius
                        color: soundTap.pressed ? Theme.pressed : soundHover.hovered || soundMenu.isOpen ? Theme.selected : Qt.rgba(1, 1, 1, 0.06)
                        border.width: 1
                        border.color: soundMenu.isOpen ? Theme.borderStrong : Theme.border
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                        Icon {
                            id: soundGlyph
                            anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
                            glyph: Theme.icon.sound; size: 13
                            color: Theme.textSecondary
                        }
                        Text {
                            anchors { left: soundGlyph.right; leftMargin: 10; right: soundChevron.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
                            text: page.soundLabel
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody
                            color: Theme.text
                        }
                        Icon {
                            id: soundChevron
                            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                            glyph: Theme.icon.chevronDown; size: 11
                            color: Theme.textSecondary
                        }
                        HoverHandler { id: soundHover; cursorShape: Qt.PointingHandCursor }
                        MouseArea {
                            id: soundTap
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton
                            onClicked: soundMenu.isOpen ? soundMenu.close() : soundMenu.openAt(soundButton, soundButton.width - soundMenu.cardWidth, soundButton.height + 4)
                        }
                    }
                }
            }

            // ── APPEARANCE ──────────────────────────────────────────
            SectionLabel { text: "Appearance"; leftPadding: 4; topPadding: 14 }
            SettingCard {
                SettingRow {
                    label: "Background blur"
                    hint: "The glass behind the Full App and the overlay"
                    Row {
                        spacing: 12
                        Slider { id: blurSlider; width: 220; value: Store.backdropBlur; onMoved: (v) => Store.backdropBlur = v }
                        Text { anchors.verticalCenter: parent.verticalCenter; width: 38; horizontalAlignment: Text.AlignRight
                               text: Math.round(Store.backdropBlur * 100) + "%"; font.family: Theme.font; font.pixelSize: Theme.sizeSmall; color: Theme.textMuted }
                    }
                }
                SettingRow {
                    label: "Background dim"
                    Row {
                        spacing: 12
                        Slider { id: dimSlider; width: 220; value: Store.backdropDim; onMoved: (v) => Store.backdropDim = v }
                        Text { anchors.verticalCenter: parent.verticalCenter; width: 38; horizontalAlignment: Text.AlignRight
                               text: Math.round(Store.backdropDim * 100) + "%"; font.family: Theme.font; font.pixelSize: Theme.sizeSmall; color: Theme.textMuted }
                    }
                }
                SettingRow {
                    label: "Overlay size"
                    hint: "How much of the screen the overlay's panels cover"
                    last: true
                    Row {
                        spacing: 12
                        Slider { id: sizeSlider; width: 220; from: 0.6; to: Store.overlaySizeMax; stepSize: 0.01; value: Store.overlaySize; onMoved: (v) => Store.overlaySize = v }
                        Text { anchors.verticalCenter: parent.verticalCenter; width: 38; horizontalAlignment: Text.AlignRight
                               text: Math.round(Store.overlaySize * 100) + "%"; font.family: Theme.font; font.pixelSize: Theme.sizeSmall; color: Theme.textMuted }
                    }
                }
            }

            // ── FILES ───────────────────────────────────────────────
            SectionLabel { text: "Files"; leftPadding: 4; topPadding: 14 }
            SettingCard {
                SettingRow {
                    label: "Library location"
                    hint: Library.rootPath + "\n" + page.usageText
                    last: page.filesError === ""
                    Row {
                        spacing: 8
                        TextButton { id: openFolder; label: "Open folder"; onClicked: Library.openFolder() }
                        TextButton { id: changeFolder; label: "Change…"; onClicked: folderDialog.open() }
                    }
                }
                Item {
                    visible: page.filesError !== ""
                    width: parent.width; height: visible ? 34 : 0
                    Text {
                        anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                        text: page.filesError
                        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                        color: Qt.rgba(0.93, 0.42, 0.44, 1)
                    }
                }
            }

            // ── BACKUPS ─────────────────────────────────────────────
            SectionLabel { text: "Backups"; leftPadding: 4; topPadding: 14 }
            SettingCard {
                SettingRow {
                    label: "Automatic daily backup"
                    Switch { id: autoSwitch; checked: Store.autoBackup; onToggled: Store.autoBackup = !Store.autoBackup }
                }
                SettingRow {
                    label: "Keep the last"
                    Row {
                        spacing: 8
                        InfoChip {
                            id: keepButton
                            label: Store.keepBackups + " backups"
                            glyph: Theme.icon.chevronDown
                            onClicked: keepMenu.openAt(keepButton, 0, keepButton.height + 4)
                        }
                    }
                }
                SettingRow {
                    label: "Manual backup"
                    hint: "Adds a backup of your data to the list below"
                    last: backupRepeater.count === 0
                    TextButton { id: backUpNow; primary: true; label: "Back up now"; onClicked: Store.backUpNow() }
                }
                Repeater {
                    id: backupRepeater
                    model: Store.backups
                    Item {
                        id: backupRow
                        required property var modelData
                        required property int index
                        property alias restoreButton: restore
                        property alias moreButton: more
                        width: parent.width; height: 46
                        Rectangle {
                            anchors { left: parent.left; right: parent.right; top: parent.top }
                            height: 1; color: Qt.rgba(1, 1, 1, 0.06)
                        }
                        Icon {
                            id: cloud
                            anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                            glyph: Theme.icon.cloud; size: 14; color: Theme.textMuted
                        }
                        Text {
                            anchors { left: cloud.right; leftMargin: 12; verticalCenter: parent.verticalCenter }
                            text: Dates.longDate(backupRow.modelData.at) + "  " + Dates.clock(backupRow.modelData.at)
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody
                            color: Theme.text
                        }
                        Text {
                            anchors { right: restore.left; rightMargin: 16; verticalCenter: parent.verticalCenter }
                            text: backupRow.modelData.sizeMb.toFixed(1) + " MB"
                            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                            color: Theme.textMuted
                        }
                        TextButton {
                            id: restore
                            anchors { right: more.left; rightMargin: 4; verticalCenter: parent.verticalCenter }
                            label: "Restore"
                            onClicked: page.ask("Restore this backup?",
                                                "Restore the backup from " + Dates.longDate(backupRow.modelData.at) + "? Your current data is backed up first, then replaced by it.",
                                                "Restore", () => Store.restoreBackup(backupRow.modelData.id))
                        }
                        IconButton {
                            id: more
                            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                            glyph: Theme.icon.more; iconSize: 13
                            onClicked: { page.menuBackupId = backupRow.modelData.id; backupMenu.openAt(more, -140, more.height + 2) }
                        }
                    }
                }
            }
            Item { width: 1; height: 24 }
        }
    }

    // The notification sound list: hovering a row (after a short delay) or choosing it plays a preview
    ContextMenu {
        id: soundMenu
        cardWidth: 210
        hoverDelay: 300
        model: page.soundOptions.map(o => ({ key: o.path, label: o.label, glyph: Store.notificationSound === o.path ? Theme.icon.check : "" }))
        onRowHovered: (key) => Sounds.preview(key)
        onChosen: (key) => {
            Store.notificationSound = key
            Sounds.preview(key)
        }
        onIsOpenChanged: if (!isOpen) Sounds.stop()
    }
    // "Keep the last N backups" and a backup's ⋯ menu
    ContextMenu {
        id: keepMenu
        cardWidth: 150
        model: [3, 5, 7, 14, 30].map(n => ({ key: String(n), label: n + " backups", glyph: Store.keepBackups === n ? Theme.icon.check : "" }))
        onChosen: (key) => Store.keepBackups = parseInt(key, 10)
    }
    ContextMenu {
        id: backupMenu
        cardWidth: 150
        model: [{ key: "delete", label: "Delete", glyph: Theme.icon.trash, danger: true }]
        onChosen: () => Store.deleteBackup(page.menuBackupId)
    }
}
