import QtQuick

// The first-launch tour: a centered card over a dimmed and blurred page (the page blurs while
// `progress` > 0, see Main.qml). Three steps: Welcome (asks for a name), Notes & tasks, Files & focus.
// It is modal: the scrim is a ClickBlocker that swallows every click (it never closes the tour),
// and only Skip, or Get started on the last step, finishes it. Everything clickable is a MouseArea.
// It shows while the setting `tourDone` is false and sets it when finished or skipped; a name that
// was typed is saved as `userName`.
Item {
    id: tour
    readonly property int stepCount: 3
    property int step: 0                       // 0..2
    readonly property bool isOpen: !Store.tourDone
    readonly property bool lastStep: step === stepCount - 1
    property real progress: isOpen ? 1 : 0
    property rect passThrough: Qt.rect(0, 0, 0, 0)   // the window's own buttons stay usable (window coordinates)
    property alias nameBox: nameField
    property alias card: card
    property alias nextLabel: nextButton.label
    property alias introText: intro.text
    readonly property string stepLabel: "Step " + (step + 1) + " of " + stepCount
    signal changeHotkeyRequested()

    Behavior on progress { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
    visible: progress > 0.001

    function reset() {
        step = 0
        nameField.text = Store.userName
        focusStep()
    }
    function goTo(index) {
        step = Math.max(0, Math.min(stepCount - 1, index))
        focusStep()
    }
    function focusStep() {
        if (step === 0)
            nameField.input.forceActiveFocus()
        else
            card.forceActiveFocus()
    }
    function next() {
        if (lastStep)
            finish(false)
        else
            goTo(step + 1)
    }
    function skip() { finish(false) }
    // Finishing or skipping: the tour is done, and a typed name is kept for the greeting
    function finish(thenChangeHotkey) {
        const name = nameField.text.trim()
        if (name !== "")
            Store.userName = name
        Store.tourDone = true
        if (thenChangeHotkey)
            changeHotkeyRequested()
    }
    onIsOpenChanged: if (isOpen) reset()
    Component.onCompleted: if (isOpen) reset()

    // Scrim: modal. Nothing under it reacts, and it never closes the tour.
    Rectangle {
        anchors.fill: parent
        color: Theme.scrim
        opacity: tour.progress
        ClickBlocker {
            anchors.fill: parent
            containmentMask: QtObject {
                function contains(point: point): bool {
                    const r = tour.passThrough
                    return !(point.x >= r.x && point.x <= r.x + r.width && point.y >= r.y && point.y <= r.y + r.height)
                }
            }
        }
    }

    Rectangle {
        id: card
        width: Math.min(540, tour.width - 48)
        // as tall as the current step (animated); the top edge is placed for the tallest step, so it stays put
        readonly property real stepHeight: [welcome, notes, files][tour.step].implicitHeight
        readonly property real fullHeight: content.implicitHeight + 56 + footer.height
        height: Math.min(stepHeight + 56 + footer.height, tour.height - 48)
        Behavior on height { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
        x: (tour.width - width) / 2
        y: Math.max(24, (tour.height - Math.min(fullHeight, tour.height - 48)) * 0.42)
        radius: Theme.radius
        color: Theme.raised
        border.width: 1
        border.color: Theme.borderStrong
        opacity: tour.progress
        scale: 0.97 + 0.03 * tour.progress
        focus: true
        Keys.onReturnPressed: tour.next()
        Keys.onEnterPressed: tour.next()

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // keep clicks inside the card

        // ── The three steps ─────────────────────────────────────────
        Item {
            id: content
            x: 28; y: 26
            width: parent.width - 56
            implicitHeight: Math.max(welcome.implicitHeight, notes.implicitHeight, files.implicitHeight)

            // Step 1: Welcome
            Column {
                id: welcome
                width: parent.width
                spacing: 14
                visible: opacity > 0.01
                opacity: tour.step === 0 ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                Text {
                    text: "Welcome"
                    font.family: Theme.font; font.pixelSize: 26; font.weight: Font.Light
                    color: Theme.text
                }
                Text {
                    text: "What should we call you?"
                    font.family: Theme.font; font.pixelSize: Theme.sizeTitle
                    color: Theme.textSecondary
                }
                Rectangle {
                    width: parent.width; height: 40
                    radius: Theme.radius
                    color: Qt.rgba(1, 1, 1, 0.06)
                    border.width: 1
                    border.color: nameField.input.activeFocus ? Theme.borderStrong : Theme.border
                    TextBox {
                        id: nameField
                        anchors { left: parent.left; leftMargin: 14; right: parent.right; rightMargin: 14; verticalCenter: parent.verticalCenter }
                        placeholder: "Your name (optional)"
                        pixelSize: Theme.sizeTitle
                        multiline: false
                        onSubmitted: tour.next()
                    }
                    MouseArea { anchors.fill: parent; z: -1; acceptedButtons: Qt.LeftButton; onClicked: nameField.input.forceActiveFocus() }
                }
                Item { width: 1; height: 4 }
                Text {
                    id: intro
                    width: parent.width
                    textFormat: Text.StyledText
                    wrapMode: Text.Wrap
                    lineHeight: 1.25
                    text: "Press <b>" + Store.hotkey.replace(/\+/g, " + ") + "</b> anytime to open your quick overlay. "
                          + "Press it twice quickly to open this window."
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
                    color: Theme.textSecondary
                }
                TextButton {
                    id: changeHotkey
                    labelColor: Theme.text
                    label: "Change hotkey"
                    onClicked: tour.finish(true)
                }
            }

            // Step 2: Notes & tasks
            Column {
                id: notes
                width: parent.width
                spacing: 14
                visible: opacity > 0.01
                opacity: tour.step === 1 ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                Text {
                    text: "Notes & tasks"
                    font.family: Theme.font; font.pixelSize: 26; font.weight: Font.Light
                    color: Theme.text
                }
                // a tiny picture: a note card and a task row
                Row {
                    spacing: 12
                    height: 82
                    GlassPanel {
                        width: 190; height: 82
                        fill: Theme.tint("slate")
                        Column {
                            x: 12; y: 12
                            width: parent.width - 24
                            spacing: 5
                            Text {
                                text: "Meeting notes"
                                font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1; font.weight: Font.Medium
                                color: Theme.text
                            }
                            Text {
                                width: parent.width
                                text: "- Budget review for Q3\n- Hiring: two designers"
                                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                                lineHeight: 1.2
                                color: Theme.textSecondary
                            }
                        }
                    }
                    Rectangle {
                        width: notes.width - 190 - 12; height: 40
                        anchors.verticalCenter: parent.verticalCenter
                        radius: Theme.radius
                        color: Qt.rgba(1, 1, 1, 0.07)
                        Rectangle {
                            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
                            width: 3; radius: 1.5
                            color: Theme.priorityColor("high")
                        }
                        Checkbox { id: sampleCheck; anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter } }
                        Text {
                            anchors { left: sampleCheck.right; leftMargin: 12; right: sampleDue.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
                            text: "Call mom"
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
                            color: Theme.text
                        }
                        Text {
                            id: sampleDue
                            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                            text: "17:00"
                            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                            color: Theme.textSecondary
                        }
                    }
                }
                TourLine {
                    glyph: Theme.icon.note
                    text: "The <b>Take a note</b> bar is always at the top of Notes. Click it and start typing."
                }
                TourLine {
                    glyph: Theme.icon.tasks
                    text: "Smart typing: write <b>Call mom tomorrow 5pm !high</b> and the date and priority are picked out for you."
                }
                TourLine {
                    glyph: Theme.icon.calendar
                    text: "Drag a task onto another day to reschedule it."
                }
            }

            // Step 3: Files & focus
            Column {
                id: files
                width: parent.width
                spacing: 14
                visible: opacity > 0.01
                opacity: tour.step === 2 ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                Text {
                    text: "Files & focus"
                    font.family: Theme.font; font.pixelSize: 26; font.weight: Font.Light
                    color: Theme.text
                }
                TourLine {
                    glyph: Theme.icon.folder
                    text: "<b>Your Files library</b> keeps what you add: drag files in, click one to open it."
                }
                TourLine {
                    glyph: Theme.icon.focus
                    text: "The <b>focus timer</b> paces your work in sessions and breaks, and goes fullscreen when you want it calm."
                }
                TourLine {
                    glyph: Theme.icon.layout
                    text: "In the overlay, the <b>layout button</b> next to the search bar arranges your panels, or drag a panel by its header."
                }
            }
        }

        // ── Footer: dots, Skip and Next ─────────────────────────────
        Item {
            id: footer
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: 62
            Rectangle {
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: 1; color: Theme.border
            }
            Row {
                anchors { left: parent.left; leftMargin: 28; verticalCenter: parent.verticalCenter }
                spacing: 12
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 7
                    Repeater {
                        model: tour.stepCount
                        Rectangle {
                            required property int index
                            width: 8; height: 8; radius: 4
                            color: index === tour.step ? Theme.accent : Qt.rgba(1, 1, 1, 0.22)
                            Behavior on color { ColorAnimation { duration: Theme.fast } }
                        }
                    }
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: tour.stepLabel
                    font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                    color: Theme.textMuted
                }
            }
            Row {
                anchors { right: parent.right; rightMargin: 20; verticalCenter: parent.verticalCenter }
                spacing: 8
                TextButton { id: skipButton; label: "Skip"; labelColor: Theme.textSecondary; onClicked: tour.skip() }
                TextButton {
                    id: nextButton
                    primary: true
                    label: tour.lastStep ? "Get started" : "Next ›"
                    onClicked: tour.next()
                }
            }
        }
    }

    // One line of explanation: a glyph and a sentence (styled text)
    component TourLine: Item {
        id: line
        property string glyph: ""
        property string text: ""
        width: parent.width
        height: Math.max(26, lineText.implicitHeight)
        Icon {
            id: lineGlyph
            anchors { left: parent.left; top: parent.top; topMargin: 2 }
            width: 22
            glyph: line.glyph; size: 15; color: Theme.textSecondary
        }
        Text {
            id: lineText
            anchors { left: lineGlyph.right; leftMargin: 10; right: parent.right; top: parent.top }
            text: line.text
            textFormat: Text.StyledText
            wrapMode: Text.Wrap
            lineHeight: 1.2
            font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
            color: Theme.textSecondary
        }
    }

    // Window coordinates of the controls (the self-test clicks them)
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function probe(name) {
        const item = ({ next: nextButton, skip: skipButton, name: nameField, change: changeHotkey, card: card })[name]
        return item ? center(item) : null
    }
}
