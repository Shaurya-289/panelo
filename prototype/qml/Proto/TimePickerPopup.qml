import QtQuick
import "dates.js" as Dates

// Small glass popup. Fill the window with it; openFor(item) places the card next to the item that
// asked for it. Two forms:
//  - "simple" (the overlay's reminder field): quick chips and an HH:MM field.
//  - "full" (Tasks and Reminders in the Full App): quick days (Today / Tomorrow / Next week /
//    No date), a small month grid and an HH:MM field.
// Both report the choice with pickedAt(ms, hasTime); "No date" reports pickedAt(null, false).
// Everything clickable is a MouseArea: the click-away layer and the card swallow their clicks.
Item {
    id: popup
    property bool isOpen: false
    property string mode: "simple"     // "simple" | "full"
    property bool ready: true          // false: chips are dimmed (no reminder title yet)
    property bool requireTime: false   // full: a time is needed (reminders), so there is no "No date"
    property real shown: isOpen ? 1 : 0
    property bool invalid: false
    property var selectedDay: null     // full: the chosen day (ms at 00:00), or null
    property int viewYear: 2026
    property int viewMonth: 0
    property alias card: card
    signal pickedAt(var ms, bool hasTime)
    signal rejected()

    Behavior on shown { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    visible: shown > 0.001

    readonly property bool full: mode === "full"

    // `initialMs` / `hasTime` preselect the day and the time in the full form.
    function openFor(item, initialMs, hasTime) {
        invalid = false
        if (full) {
            const base = initialMs === undefined || initialMs === null ? null : Dates.startOfDay(initialMs)
            selectedDay = base
            const shownDay = new Date(base === null ? Date.now() : base)
            viewYear = shownDay.getFullYear()
            viewMonth = shownDay.getMonth()
            field.text = hasTime && initialMs ? Dates.clock(initialMs) : ""
        } else {
            field.text = ""
        }
        const p = item.mapToItem(popup, 0, 0)
        const h = card.height
        card.x = Math.max(8, Math.min(popup.width - card.width - 8, p.x + item.width - card.width))
        if (full)
            card.y = Math.max(8, Math.min(popup.height - h - 8, p.y + item.height + 8 + h > popup.height - 8 ? p.y - h - 8 : p.y + item.height + 8))
        else
            card.y = p.y - h - 8 >= 8 ? p.y - h - 8 : Math.min(popup.height - h - 8, p.y + item.height + 8)
        isOpen = true
        if (ready && !full)
            field.input.forceActiveFocus()
    }
    function close() { isOpen = false }
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    // used by the self-test: window coordinates of the controls
    function probe(name) {
        const item = ({ field: field, set: setButton, card: card, today: todayChip, tomorrow: tomorrowChip,
                        nextWeek: nextWeekChip, noDate: noDateChip })[name]
        return item ? center(item) : null
    }

    function choose(ms, hasTime) {
        if (!ready) {
            rejected()
            return
        }
        isOpen = false
        pickedAt(ms, hasTime)
    }
    // simple form
    function inMinutes(m) { choose(Date.now() + m * 60000, true) }
    function atClock(h, m) {
        let at = Dates.dayAt(0, h, m)
        if (at <= Date.now())
            at = Dates.addDays(at, 1)
        choose(at, true)
    }
    function parsedClock() {
        const match = /^\s*(\d{1,2})[:.](\d{2})\s*$/.exec(field.text)
        if (!match || Number(match[1]) > 23 || Number(match[2]) > 59)
            return null
        return { h: Number(match[1]), m: Number(match[2]) }
    }
    function submitField() {
        const t = parsedClock()
        if (t === null) {
            invalid = true
            return
        }
        if (!full) {
            atClock(t.h, t.m)
            return
        }
        commitFull()
    }

    // full form: the day goes together with the time field (empty: a date only)
    function commitFull() {
        const t = parsedClock()
        const typed = field.text.trim() !== ""
        if ((typed && t === null) || (requireTime && t === null)) {
            invalid = true
            return
        }
        const day = selectedDay === null ? Dates.dayAt(0, 0, 0) : selectedDay
        if (t === null) {
            choose(selectedDay === null ? null : day, false)
            return
        }
        let ms = Dates.withClock(day, t.h, t.m)
        if (selectedDay === null && ms <= Date.now())
            ms = Dates.addDays(ms, 1)
        choose(ms, true)
    }
    function quickDay(offset) {
        selectedDay = Dates.dayAt(offset, 0, 0)
        const d = new Date(selectedDay)
        viewYear = d.getFullYear(); viewMonth = d.getMonth()
        if (parsedClock() !== null || (!requireTime && field.text.trim() === ""))
            commitFull()
        else
            invalid = true
    }
    function noDate() { choose(null, false) }

    // Click-away layer
    ClickBlocker { anchors.fill: parent; onDismissed: popup.close() }

    Rectangle {
        id: card
        width: popup.full ? 292 : 232
        height: content.implicitHeight + 24
        radius: Theme.radius
        color: Qt.rgba(0.11, 0.115, 0.135, 1)   // opaque, unlike Theme.raised
        border.width: 1
        border.color: Theme.borderStrong
        opacity: popup.shown
        scale: 0.96 + 0.04 * popup.shown

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // keep clicks inside the card

        component Chip: Rectangle {
            id: chip
            property string label: ""
            property string detail: ""
            signal clicked()
            width: parent.width; height: 32
            radius: Theme.radius
            color: chipTap.pressed ? Theme.pressed : chipHover.hovered ? Theme.hover : "transparent"
            opacity: popup.ready ? 1 : 0.4
            Behavior on color { ColorAnimation { duration: Theme.fast } }
            Text {
                anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                text: chip.label
                font.family: Theme.font; font.pixelSize: Theme.sizeBody
                color: Theme.text
            }
            Text {
                anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                text: chip.detail
                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                color: Theme.textMuted
            }
            HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
            MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; id: chipTap; onClicked: chip.clicked() }
        }

        // a compact pill for the quick days
        component DayChip: Rectangle {
            id: dayChip
            property string label: ""
            property bool active: false
            signal clicked()
            height: 28
            width: dayLabel.implicitWidth + 18
            radius: Theme.radius
            color: dayTap.pressed ? Theme.pressed : active ? Theme.selected : dayHover.hovered ? Theme.hover : Qt.rgba(1, 1, 1, 0.05)
            border.width: 1
            border.color: active ? Theme.borderStrong : Theme.border
            Behavior on color { ColorAnimation { duration: Theme.fast } }
            Text {
                id: dayLabel
                anchors.centerIn: parent
                text: dayChip.label
                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                color: Theme.text
            }
            HoverHandler { id: dayHover; cursorShape: Qt.PointingHandCursor }
            MouseArea { id: dayTap; anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: dayChip.clicked() }
        }

        Column {
            id: content
            x: 12; y: 12
            width: parent.width - 24
            spacing: 2

            // ── simple form ─────────────────────────────────────────
            Column {
                width: parent.width
                spacing: 2
                visible: !popup.full
                height: visible ? implicitHeight : 0
                SectionLabel { text: "Remind me"; bottomPadding: 6; leftPadding: 2 }
                Chip { label: "In 15 min"; onClicked: popup.inMinutes(15) }
                Chip { label: "In 1 h"; onClicked: popup.inMinutes(60) }
                Chip { label: "Tonight"; detail: "20:00"; onClicked: popup.atClock(20, 0) }
                Chip { label: "Tomorrow"; detail: "09:00"; onClicked: popup.atClock(9, 0) }
                Item { width: 1; height: 6 }
            }

            // ── full form ───────────────────────────────────────────
            Column {
                width: parent.width
                spacing: 8
                visible: popup.full
                height: visible ? implicitHeight : 0

                Row {
                    spacing: 6
                    DayChip { id: todayChip; label: "Today"; active: popup.selectedDay === Dates.dayAt(0, 0, 0); onClicked: popup.quickDay(0) }
                    DayChip { id: tomorrowChip; label: "Tomorrow"; active: popup.selectedDay === Dates.dayAt(1, 0, 0); onClicked: popup.quickDay(1) }
                    DayChip { id: nextWeekChip; label: "Next week"; active: popup.selectedDay === Dates.nextMonday(); onClicked: popup.quickDay(Dates.dayDiff(Dates.nextMonday())) }
                    DayChip { id: noDateChip; visible: !popup.requireTime; label: "No date"; active: popup.selectedDay === null; onClicked: popup.noDate() }
                }

                // month header
                Item {
                    width: parent.width; height: 26
                    IconButton {
                        anchors.left: parent.left
                        width: 26; height: 26
                        glyph: Theme.icon.chevronLeft; iconSize: 11
                        onClicked: { if (popup.viewMonth === 0) { popup.viewMonth = 11; popup.viewYear -= 1 } else popup.viewMonth -= 1 }
                    }
                    Text {
                        anchors.centerIn: parent
                        text: Dates.MONTH_NAMES[popup.viewMonth] + " " + popup.viewYear
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody; font.weight: Font.Medium
                        color: Theme.text
                    }
                    IconButton {
                        anchors.right: parent.right
                        width: 26; height: 26
                        glyph: Theme.icon.chevronRight; iconSize: 11
                        onClicked: { if (popup.viewMonth === 11) { popup.viewMonth = 0; popup.viewYear += 1 } else popup.viewMonth += 1 }
                    }
                }
                // weekday letters + the six-week grid
                Grid {
                    id: grid
                    columns: 7
                    readonly property real cell: parent.width / 7
                    Repeater {
                        model: ["M", "T", "W", "T", "F", "S", "S"]
                        Text {
                            required property string modelData
                            width: grid.cell; height: 18
                            horizontalAlignment: Text.AlignHCenter
                            text: modelData
                            font.family: Theme.font; font.pixelSize: 11
                            color: Theme.textMuted
                        }
                    }
                    Repeater {
                        model: Dates.monthCells(popup.viewYear, popup.viewMonth)
                        Item {
                            id: dayCell
                            required property var modelData
                            readonly property bool inMonth: new Date(modelData).getMonth() === popup.viewMonth
                            readonly property bool selected: popup.selectedDay === modelData
                            readonly property bool isToday: modelData === Dates.dayAt(0, 0, 0)
                            width: grid.cell; height: 30
                            Rectangle {
                                anchors.centerIn: parent
                                width: 28; height: 26; radius: Theme.radius
                                color: dayCell.selected ? Theme.accent : dayMouse.pressed ? Theme.pressed : dayMouse.containsMouse ? Theme.hover : "transparent"
                                border.width: dayCell.isToday && !dayCell.selected ? 1 : 0
                                border.color: Theme.borderStrong
                            }
                            Text {
                                anchors.centerIn: parent
                                text: new Date(dayCell.modelData).getDate()
                                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                                color: dayCell.selected ? Theme.accentInk : dayCell.inMonth ? Theme.text : Theme.textMuted
                            }
                            MouseArea {
                                id: dayMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton
                                cursorShape: Qt.PointingHandCursor
                                onClicked: popup.selectedDay = dayCell.modelData
                            }
                        }
                    }
                }
            }

            // the HH:MM field, shared by both forms
            Rectangle {
                width: parent.width; height: 34
                radius: Theme.radius
                color: Qt.rgba(1, 1, 1, 0.05)
                border.width: 1
                border.color: popup.invalid ? Theme.danger : (field.input.activeFocus ? Theme.borderStrong : Theme.border)
                Icon {
                    id: clockGlyph
                    anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                    glyph: Theme.icon.clock; size: 12; color: Theme.textMuted
                }
                TextBox {
                    id: field
                    anchors { left: clockGlyph.right; leftMargin: 10; right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                    placeholder: popup.full ? (popup.requireTime ? "HH:MM" : "HH:MM (optional)") : "HH:MM"
                    multiline: false
                    onTextChanged: popup.invalid = false
                    onSubmitted: popup.submitField()
                }
                MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; z: -1; onClicked: field.input.forceActiveFocus() }
            }

            Item {
                visible: popup.full
                width: parent.width; height: visible ? 30 : 0
                TextButton {
                    id: setButton
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    primary: true; label: "Set"
                    onClicked: popup.commitFull()
                }
                Text {
                    anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                    visible: popup.selectedDay !== null
                    text: popup.selectedDay === null ? "" : Dates.longDate(popup.selectedDay)
                    font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                    color: Theme.textMuted
                }
            }
        }
    }
}
