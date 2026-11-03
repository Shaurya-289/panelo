import QtQuick
import QtQuick.Shapes
import "dates.js" as Dates

// The Focus view: a big, calm Pomodoro timer (the same state as the rail card and the overlay),
// a stats strip from the focus history, and the other timers (several can run at once) with
// preset chips to start new ones.
Item {
    id: page

    readonly property real contentW: Math.min(page.width - 48, 760)
    readonly property real ringSize: Math.max(200, Math.min(300, page.height - 500, contentW))
    readonly property var stats: Store.focusStats
    readonly property real progress: Store.focusTotal > 0 ? 1 - Store.focusRemaining / Store.focusTotal : 0
    readonly property bool menuOpen: lengths.isOpen || newTimer.isOpen || timerMenu.isOpen
    property int menuTimerId: -1
    signal fullscreenRequested()

    // running timers first
    readonly property var sortedTimers: {
        const live = t => t.running && t.remaining > 0
        return Store.timers.filter(live).concat(Store.timers.filter(t => !live(t)))
    }

    function fmt(s) {
        const m = Math.floor(s / 60), r = s % 60
        return (m < 10 ? "0" : "") + m + ":" + (r < 10 ? "0" : "") + r
    }
    function handleEscape() {
        return lengths.handleEscape() || newTimer.handleEscape() || timerMenu.handleEscape()
    }

    // used by the self-test and the demos: window coordinates of the controls
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function probe(name, index) {
        const item = ({
            gear: gearButton, play: playButton, skip: skipButton, reset: resetButton, fullscreen: fullscreenButton,
            startOver: startOverButton, newTimer: newChip,
            focusPlus: focusStepper.plus, focusMinus: focusStepper.minus, focusField: focusStepper.field,
            shortPlus: shortStepper.plus, longPlus: longStepper.plus, longMinus: longStepper.minus,
            nameField: nameBox, minutesField: minutesStepper.field, start: startButton, ring: ring
        })[name]
        if (item)
            return center(item)
        if (name === "preset") return center(presets.itemAt(index))
        if (name === "timerCard" || name === "timerPlay" || name === "timerMenu") {
            const slot = page.sortedTimers.findIndex(t => t.id === index)
            const card = slot < 0 ? null : timerRepeater.itemAt(slot)
            if (!card)
                return null
            return center(name === "timerCard" ? card : name === "timerPlay" ? card.playButton : card.menuButton)
        }
        if (name === "menuItem") return center(timerMenu.itemAtIndex(index))
        return null
    }
    function demoLengths() { lengths.openFor(gearButton) }
    function demoNewTimer() { newTimer.openFor(newChip, false); nameBox.text = "Pasta"; minutesStepper.valueEdited(12) }
    readonly property bool resetHintShown: resetButton.hintShown
    readonly property bool lengthsOpen: lengths.isOpen
    readonly property bool newTimerOpen: newTimer.isOpen
    readonly property bool timerMenuOpen: timerMenu.isOpen
    readonly property string lengthsText: focusStepper.field.text + "," + shortStepper.field.text + "," + longStepper.field.text

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
            width: page.contentW
            spacing: 18

            // ── The big timer ──────────────────────────────────────
            Item {
                width: parent.width
                height: page.ringSize + 4
                Item {
                    id: ring
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: page.ringSize; height: page.ringSize
                    Shape {
                        anchors.fill: parent
                        layer.enabled: true
                        layer.samples: 4
                        ShapePath {
                            strokeColor: Qt.rgba(1, 1, 1, 0.10)
                            strokeWidth: 3
                            fillColor: "transparent"
                            PathAngleArc { centerX: ring.width / 2; centerY: ring.height / 2; radiusX: ring.width / 2 - 4; radiusY: ring.height / 2 - 4; startAngle: 0; sweepAngle: 360 }
                        }
                        ShapePath {
                            strokeColor: Theme.accent
                            strokeWidth: 3
                            fillColor: "transparent"
                            capStyle: ShapePath.RoundCap
                            PathAngleArc {
                                centerX: ring.width / 2; centerY: ring.height / 2
                                radiusX: ring.width / 2 - 4; radiusY: ring.height / 2 - 4
                                startAngle: -90
                                sweepAngle: Math.max(0.01, 360 * page.progress)
                                Behavior on sweepAngle { NumberAnimation { duration: 400 } }
                            }
                        }
                    }
                    Text {
                        anchors { horizontalCenter: parent.horizontalCenter; verticalCenter: parent.verticalCenter; verticalCenterOffset: -12 }
                        text: page.fmt(Store.focusRemaining)
                        font.family: Theme.font
                        font.pixelSize: Math.round(page.ringSize * 0.32)
                        font.weight: Font.Light
                        color: Theme.text
                    }
                    Text {
                        anchors { horizontalCenter: parent.horizontalCenter; verticalCenter: parent.verticalCenter; verticalCenterOffset: page.ringSize * 0.22 }
                        text: Store.focusLabel
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody
                        color: Theme.textSecondary
                    }
                }
            }

            // controls: reset, play, skip, then the lengths gear and the fullscreen button
            Item {
                width: parent.width; height: 40
                Row {
                    anchors.centerIn: parent
                    spacing: 10
                    IconButton { id: resetButton; width: 38; height: 38; glyph: Theme.icon.reset; iconSize: 15; hint: "Reset session"; onClicked: Store.focusReset() }
                    Rectangle {
                        id: playButton
                        width: 46; height: 38; radius: Theme.radius
                        color: playTap.pressed ? "#cfd0d4" : playHover.hovered ? "#e6e7ea" : Theme.accent
                        Icon {
                            anchors.centerIn: parent
                            glyph: Store.focusRunning ? Theme.icon.pause : Theme.icon.play
                            size: 15; color: Theme.accentInk
                        }
                        HoverHandler { id: playHover; cursorShape: Qt.PointingHandCursor }
                        MouseArea { id: playTap; anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: Store.focusRunning = !Store.focusRunning }
                    }
                    IconButton { id: skipButton; width: 38; height: 38; glyph: Theme.icon.skip; iconSize: 15; onClicked: Store.focusSkip() }
                }
                IconButton {
                    id: gearButton
                    anchors { left: parent.horizontalCenter; leftMargin: 100; verticalCenter: parent.verticalCenter }
                    width: 38; height: 38
                    glyph: Theme.icon.settings; iconSize: 15
                    active: lengths.isOpen
                    onClicked: lengths.isOpen ? lengths.close() : lengths.openFor(gearButton)
                }
                IconButton {
                    id: fullscreenButton
                    anchors { left: gearButton.right; leftMargin: 6; verticalCenter: parent.verticalCenter }
                    width: 38; height: 38
                    glyph: Theme.icon.expand; iconSize: 15
                    hint: "Fullscreen"
                    onClicked: page.fullscreenRequested()
                }
            }

            // phase dots
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 10
                Repeater {
                    model: 8
                    Rectangle {
                        required property int index
                        anchors.verticalCenter: parent.verticalCenter
                        width: index % 2 === 0 ? 10 : 5
                        height: width; radius: width / 2
                        color: index < Store.focusPhase ? Theme.accent
                             : index === Store.focusPhase ? Qt.rgba(1, 1, 1, 0.6) : Qt.rgba(1, 1, 1, 0.16)
                    }
                }
            }

            // stats strip
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 14
                topPadding: 4
                Text {
                    text: "Today  " + page.stats.sessionsToday + (page.stats.sessionsToday === 1 ? " session" : " sessions") + "  ·  " + Dates.humanMinutes(page.stats.minutesToday)
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.textSecondary
                }
                Text { text: "·"; font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.textMuted }
                Text {
                    text: "Week  " + Dates.humanMinutes(page.stats.weekMinutes)
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.textSecondary
                }
                Text { text: "·"; font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.textMuted }
                Text {
                    text: "🔥 " + page.stats.streak + "-day streak"
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.textSecondary
                }
            }

            Rectangle { width: parent.width; height: 1; color: Theme.border }

            // ── Timers ────────────────────────────────────────────
            SectionLabel { text: "Timers"; leftPadding: 2 }

            Flow {
                width: parent.width
                spacing: Theme.gap
                visible: page.sortedTimers.length > 0
                Repeater {
                    id: timerRepeater
                    model: page.sortedTimers.length
                    GlassPanel {
                        id: card
                        required property int index
                        readonly property var t: page.sortedTimers[index] || ({ id: -1, name: "", remaining: 0, total: 1, running: false })
                        readonly property bool live: t.running && t.remaining > 0
                        property alias playButton: playTimer
                        property alias menuButton: moreButton
                        width: Math.floor((column.width - 2 * Theme.gap) / 3)
                        height: 112
                        hovered: hover.hovered
                        HoverHandler { id: hover }

                        Text {
                            anchors { left: parent.left; leftMargin: 14; right: moreButton.left; rightMargin: 6; top: parent.top; topMargin: 12 }
                            text: card.t.name
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody; font.weight: Font.Medium
                            color: Theme.textSecondary
                        }
                        IconButton {
                            id: moreButton
                            anchors { right: parent.right; rightMargin: 6; top: parent.top; topMargin: 6 }
                            width: 26; height: 26
                            glyph: Theme.icon.more; iconSize: 13
                            onClicked: {
                                page.menuTimerId = card.t.id
                                timerMenu.openAt(moreButton, 0, moreButton.height + 2)
                            }
                        }
                        Text {
                            anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter; verticalCenterOffset: -2 }
                            text: card.t.remaining > 0 ? page.fmt(card.t.remaining) : "Done"
                            font.family: Theme.font; font.pixelSize: 28; font.weight: Font.Light
                            color: card.live ? Theme.text : Theme.textSecondary
                        }
                        IconButton {
                            id: playTimer
                            anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter; verticalCenterOffset: -2 }
                            glyph: card.live ? Theme.icon.pause : Theme.icon.play
                            onClicked: Store.toggleTimer(card.t.id)
                        }
                        Rectangle {
                            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 14; rightMargin: 14; bottomMargin: 14 }
                            height: 2; radius: 1
                            color: Qt.rgba(1, 1, 1, 0.12)
                            Rectangle {
                                width: parent.width * Math.max(0, Math.min(1, 1 - card.t.remaining / card.t.total))
                                height: parent.height; radius: 1
                                color: card.live ? Theme.accent : Qt.rgba(1, 1, 1, 0.45)
                                Behavior on width { NumberAnimation { duration: 400 } }
                            }
                        }
                    }
                }
            }
            Text {
                visible: page.sortedTimers.length === 0
                text: "No timers yet. Start one below."
                font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.textMuted
            }

            // preset chips
            Row {
                spacing: 8
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Start"
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.textMuted
                    rightPadding: 4
                }
                Repeater {
                    id: presets
                    model: Store.timerPresets
                    InfoChip {
                        required property var modelData
                        label: modelData.label
                        onClicked: Store.startPreset(modelData.id)
                    }
                }
                InfoChip {
                    id: newChip
                    glyph: Theme.icon.add
                    label: "New"
                    onClicked: newTimer.isOpen ? newTimer.close() : newTimer.openFor(newChip, false)
                }
            }
        }
    }

    // ── The lengths popover (⚙) ─────────────────────────────────────
    Popover {
        id: lengths
        cardWidth: 270
        title: "Lengths"
        component LengthRow: Item {
            id: lengthRow
            property string label: ""
            property alias stepper: stepperItem
            property int value: 0
            signal edited(int minutes)
            width: parent.width; height: 32
            Text {
                anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                text: lengthRow.label
                font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.text
            }
            Stepper {
                id: stepperItem
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                value: lengthRow.value
                minimum: 1; maximum: 180
                onValueEdited: (v) => lengthRow.edited(v)
            }
        }
        LengthRow { id: focusRow; label: "Focus"; value: Math.round(Store.focusMs / 60000); onEdited: (m) => Store.focusMs = m * 60000 }
        LengthRow { id: shortRow; label: "Short break"; value: Math.round(Store.shortMs / 60000); onEdited: (m) => Store.shortMs = m * 60000 }
        LengthRow { id: longRow; label: "Long break"; value: Math.round(Store.longMs / 60000); onEdited: (m) => Store.longMs = m * 60000 }
        Text {
            width: parent.width
            text: "Minutes."
            wrapMode: Text.Wrap
            font.family: Theme.font; font.pixelSize: 11; color: Theme.textMuted
        }
        Rectangle { width: parent.width; height: 1; color: Theme.border }
        TextButton {
            id: startOverButton
            width: parent.width
            label: "Start over from Session 1"
            onClicked: { Store.focusStartOver(); lengths.close() }
        }
    }
    property alias focusStepper: focusRow.stepper
    property alias shortStepper: shortRow.stepper
    property alias longStepper: longRow.stepper

    // ── "+ New" timer popup ─────────────────────────────────────────
    Popover {
        id: newTimer
        cardWidth: 280
        title: "New timer"
        onOpened: { nameBox.text = ""; minutesStepper.value = 10; nameBox.input.forceActiveFocus() }
        Rectangle {
            width: parent.width; height: 34
            radius: Theme.radius
            color: Qt.rgba(1, 1, 1, 0.05)
            border.width: 1
            border.color: nameBox.input.activeFocus ? Theme.borderStrong : Theme.border
            TextBox {
                id: nameBox
                anchors { left: parent.left; leftMargin: 12; right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                placeholder: "Name (optional)"
                multiline: false
                onSubmitted: startButton.clicked()
            }
            MouseArea { anchors.fill: parent; z: -1; acceptedButtons: Qt.LeftButton; onClicked: nameBox.input.forceActiveFocus() }
        }
        Item {
            width: parent.width; height: 32
            Text {
                anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                text: "Minutes"
                font.family: Theme.font; font.pixelSize: Theme.sizeBody; color: Theme.text
            }
            Stepper {
                id: minutesStepper
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                value: 10
                minimum: 1; maximum: 600
                onValueEdited: (v) => value = v
            }
        }
        Item {
            width: parent.width; height: 32
            TextButton {
                id: startButton
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                primary: true; label: "Start"
                onClicked: {
                    minutesStepper.commit()
                    Store.addTimer(nameBox.text, minutesStepper.value * 60, true)
                    newTimer.close()
                }
            }
        }
    }

    // ── ⋯ menu of a timer card ──────────────────────────────────────
    ContextMenu {
        id: timerMenu
        cardWidth: 150
        model: [
            { key: "reset", label: "Reset", glyph: Theme.icon.reset },
            { key: "delete", label: "Delete", glyph: Theme.icon.trash, danger: true }
        ]
        onChosen: (key) => {
            if (key === "reset") Store.resetTimer(page.menuTimerId)
            else Store.deleteTimer(page.menuTimerId)
        }
    }
}
