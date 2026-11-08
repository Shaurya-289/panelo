import QtQuick
import "dates.js" as Dates
import "recurrence.js" as Recur

// The reminder pop-out: grows from the clicked row, over a dim scrim; clicking outside saves and
// closes. Same pattern as NoteEditor and TaskEditor.
//   open(reminder, sourceItem), close(), dismissNow(), progress, isOpen.
// `pickDate` is set by the window: pickDate(anchorItem, ms, hasTime, requireTime, callback).
// linkRequested(link, chip) asks the window to open the linked task or note.
Item {
    id: editor
    property var reminder: null
    property rect fromRect: Qt.rect(0, 0, 0, 0)
    property real progress: 0
    property var pickDate: null
    readonly property bool isOpen: reminder !== null
    readonly property var live: reminder ? (Store.reminder(reminder.id) || reminder) : null
    readonly property var link: live ? Store.linkInfo(live.link) : null
    signal finished()
    signal linkRequested(var link, Item chip)

    visible: progress > 0.001

    function open(r, source) {
        reminder = r
        fromRect = source.mapToItem(editor, 0, 0, source.width, source.height)
        title.text = r.title
        body.text = r.message || ""
        closeAnim.stop()
        openAnim.restart()
        title.input.forceActiveFocus()
        title.input.cursorPosition = title.text.length
    }
    function close() {
        if (!reminder || closeAnim.running) return
        repeatPopup.close()
        openAnim.stop()
        closeAnim.restart()
    }
    function save() {
        if (!reminder)
            return
        const fields = { message: body.text }
        if (title.text.trim() !== "")
            fields.title = title.text.trim()
        Store.updateReminder(reminder.id, fields)
    }
    // Esc: the Repeat popup (or one of its menus) first. Returns true when it used the key.
    function handleEscape() { return repeatPopup.escapeKey() }
    // Saves and closes at once, with no animation (the window is about to open something else).
    function dismissNow() {
        if (!reminder)
            return
        openAnim.stop()
        closeAnim.stop()
        repeatPopup.close()
        save()
        reminder = null
        progress = 0
        finished()
    }

    NumberAnimation { id: openAnim; target: editor; property: "progress"; to: 1; duration: 260; easing.type: Easing.OutCubic }
    SequentialAnimation {
        id: closeAnim
        NumberAnimation { target: editor; property: "progress"; to: 0; duration: 200; easing.type: Easing.InOutCubic }
        ScriptAction { script: { editor.save(); editor.reminder = null; editor.finished() } }
    }

    // Scrim: blocks the click (the editor closes and nothing underneath reacts)
    Rectangle {
        anchors.fill: parent
        color: Theme.scrim
        opacity: editor.progress
        ClickBlocker { anchors.fill: parent; onDismissed: editor.close() }
    }

    readonly property real targetW: Math.min(560, width - 80)
    readonly property real targetH: Math.min(contentCol.implicitHeight + Theme.pad * 2 + 8, height * 0.8)
    readonly property real targetX: (width - targetW) / 2
    readonly property real targetY: Math.max(40, (height - targetH) * 0.4)
    function lerp(a, b) { return a + (b - a) * progress }

    Rectangle {
        id: sheet
        x: editor.lerp(editor.fromRect.x, editor.targetX)
        y: editor.lerp(editor.fromRect.y, editor.targetY)
        width: editor.lerp(editor.fromRect.width, editor.targetW)
        height: editor.lerp(editor.fromRect.height, editor.targetH)
        radius: Theme.radius
        color: Theme.raised
        border.width: 1
        border.color: Theme.borderStrong
        clip: true

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // swallow clicks so they don't reach the scrim

        Column {
            id: contentCol
            x: Theme.pad + 4
            y: Theme.pad + 2
            width: editor.targetW - (Theme.pad + 4) * 2
            spacing: 12
            opacity: Math.max(0, (editor.progress - 0.5) * 2)

            // bell + title
            Item {
                width: parent.width
                height: Math.max(title.implicitHeight, 24)
                Icon {
                    id: bell
                    anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                    width: 18
                    glyph: Theme.icon.reminder; size: 16; color: Theme.accent
                }
                TextBox {
                    id: title
                    anchors { left: bell.right; leftMargin: 12; right: parent.right; verticalCenter: parent.verticalCenter }
                    placeholder: "Reminder"
                    pixelSize: Theme.sizeHeading
                    weight: Font.Medium
                    multiline: false
                    onSubmitted: body.input.forceActiveFocus()
                }
            }
            // message
            Flickable {
                width: parent.width
                height: Math.max(64, Math.min(body.implicitHeight, editor.height * 0.8 - 230))
                contentHeight: body.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                TextBox {
                    id: body
                    width: parent.width
                    placeholder: "Message"
                    pixelSize: 14
                    color: Theme.textSecondary
                    onSubmitted: editor.close()
                }
                MouseArea { anchors.fill: parent; z: -1; acceptedButtons: Qt.LeftButton; onClicked: body.input.forceActiveFocus() }
            }

            // time, repeat and link
            Flow {
                width: parent.width
                spacing: 8
                InfoChip {
                    id: timeChip
                    glyph: Theme.icon.clock
                    label: editor.live ? Dates.describe(editor.live.due, true, Store.minute) : ""
                    onClicked: {
                        if (editor.pickDate && editor.live)
                            editor.pickDate(timeChip, editor.live.due, true, true, (ms) => {
                                if (ms !== null)
                                    Store.setReminderTime(editor.reminder.id, ms)
                            })
                    }
                }
                InfoChip {
                    id: repeatChip
                    glyph: Theme.icon.repeat
                    label: editor.live && editor.live.repeat ? Recur.describe(editor.live.repeat, editor.live.anchor || editor.live.due) : "Repeat"
                    maxLabelWidth: 240
                    active: repeatPopup.isOpen
                    onClicked: {
                        if (repeatPopup.isOpen) repeatPopup.close()
                        else repeatPopup.openFor(repeatChip, false)
                    }
                }
                InfoChip {
                    id: linkChip
                    visible: editor.link !== null
                    glyph: editor.link && editor.link.kind === "task" ? Theme.icon.tasks : Theme.icon.note
                    label: editor.link ? editor.link.title : ""
                    maxLabelWidth: 220
                    dimmed: editor.link !== null && editor.link.gone
                    strike: dimmed
                    onClicked: if (editor.link && !editor.link.gone) editor.linkRequested(editor.live.link, linkChip)
                }
            }
            Text {
                id: infoLine
                text: editor.live
                    ? (editor.live.timesDone > 0 ? "Done " + editor.live.timesDone + "× · " : "")
                      + (editor.live.snoozeCount > 0 ? "Snoozed " + editor.live.snoozeCount + "× · " : "")
                      + "created " + Dates.shortDay(editor.live.createdAt, Store.minute)
                      + (editor.live.done ? " · done " + Dates.shortDay(editor.live.doneAt, Store.minute) : "")
                    : ""
                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                color: Theme.textMuted
            }

            // footer
            Item {
                width: parent.width
                height: 34
                IconButton {
                    id: deleteButton
                    anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                    glyph: Theme.icon.trash
                    hoverIconColor: Theme.danger
                    onClicked: {
                        const id = editor.reminder.id
                        editor.reminder = null
                        editor.progress = 0
                        Store.deleteReminder(id)
                        editor.finished()
                    }
                }
                Row {
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    spacing: 8
                    TextButton { id: closeButton; label: "Close"; onClicked: editor.close() }
                    TextButton {
                        id: doneButton
                        primary: true
                        label: editor.live && editor.live.done ? "Reopen" : "Done"
                        onClicked: {
                            if (editor.reminder) {
                                if (editor.live.done) Store.setReminderTime(editor.reminder.id, Math.max(editor.live.due, Date.now() + 10 * 60000))
                                else Store.completeReminder(editor.reminder.id)
                            }
                            editor.close()
                        }
                    }
                }
            }
        }
    }

    RepeatPopup {
        id: repeatPopup
        rule: editor.live ? editor.live.repeat : null
        dueMs: editor.live ? editor.live.due : 0
        onRuleChosen: (rule) => { if (editor.reminder) Store.setReminderRepeat(editor.reminder.id, rule) }
    }

    // used by the self-test and the demos: window coordinates of the controls
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function demoRepeat() { repeatPopup.openFor(repeatChip, false) }
    readonly property bool repeatOpen: repeatPopup.isOpen
    readonly property bool repeatMenuOpen: repeatPopup.hasMenuOpen
    readonly property string repeatText: repeatChip.label
    readonly property string infoText: infoLine.text
    function probe(name, index) {
        if (name.startsWith("repeat."))
            return repeatPopup.probe(name.slice(7), index)
        const item = ({ title: title, body: body, time: timeChip, link: linkChip, delete: deleteButton,
                        done: doneButton, close: closeButton, sheet: sheet, repeat: repeatChip })[name]
        return item ? center(item) : null
    }
}
