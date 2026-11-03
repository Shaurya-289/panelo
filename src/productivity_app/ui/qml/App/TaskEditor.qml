import QtQuick
import "dates.js" as Dates

// The task pop-out: it grows from the clicked row to a centered card, over a dim scrim; clicking
// outside saves and closes. Same pattern as NoteEditor.
//   open(task, sourceItem), close(), progress, isOpen.
// `pickDate` is set by the window: pickDate(anchorItem, ms, hasTime, requireTime, callback).
Item {
    id: editor
    property var task: null
    property rect fromRect: Qt.rect(0, 0, 0, 0)
    property real progress: 0
    property var pickDate: null
    property string hint: ""                       // "Reminder set for ..." for a moment
    readonly property bool isOpen: task !== null
    readonly property var live: task ? (Store.task(task.id) || task) : null
    signal finished()

    visible: progress > 0.001

    function open(t, source) {
        task = t
        fromRect = source.mapToItem(editor, 0, 0, source.width, source.height)
        title.text = t.title
        body.text = t.description || ""
        hint = ""
        closeAnim.stop()
        openAnim.restart()
        title.input.forceActiveFocus()
        title.input.cursorPosition = title.text.length
    }
    function close() {
        if (!task || closeAnim.running) return
        openAnim.stop()
        closeAnim.restart()
    }
    function save() {
        if (task && title.text.trim() !== "")
            Store.updateTask(task.id, { title: title.text.trim(), description: body.text })
        else if (task)
            Store.updateTask(task.id, { description: body.text })
    }
    function handleEscape() {
        if (prioMenu.handleEscape())
            return true
        return false
    }
    readonly property bool menuOpen: prioMenu.isOpen

    NumberAnimation { id: openAnim; target: editor; property: "progress"; to: 1; duration: 260; easing.type: Easing.OutCubic }
    SequentialAnimation {
        id: closeAnim
        NumberAnimation { target: editor; property: "progress"; to: 0; duration: 200; easing.type: Easing.InOutCubic }
        ScriptAction { script: { editor.save(); editor.task = null; editor.finished() } }
    }

    // Scrim: blocks the click (the editor closes and nothing underneath reacts)
    Rectangle {
        anchors.fill: parent
        color: Theme.scrim
        opacity: editor.progress
        ClickBlocker { anchors.fill: parent; onDismissed: editor.close() }
    }

    readonly property real targetW: Math.min(580, width - 80)
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

            // checkbox + title
            Item {
                width: parent.width
                height: Math.max(title.implicitHeight, 24)
                Checkbox {
                    id: done
                    anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                    checked: editor.live ? editor.live.done : false
                    onToggled: if (editor.task) Store.toggleTask(editor.task.id)
                }
                TextBox {
                    id: title
                    objectName: "taskTitle"
                    anchors { left: done.right; leftMargin: 12; right: parent.right; verticalCenter: parent.verticalCenter }
                    placeholder: "Task"
                    pixelSize: Theme.sizeHeading
                    weight: Font.Medium
                    multiline: false
                    onSubmitted: body.input.forceActiveFocus()
                }
            }
            // description
            Flickable {
                width: parent.width
                height: Math.max(64, Math.min(body.implicitHeight, editor.height * 0.8 - 230))
                contentHeight: body.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                TextBox {
                    id: body
                    width: parent.width
                    placeholder: "Description"
                    pixelSize: 14
                    color: Theme.textSecondary
                    onSubmitted: editor.close()
                }
                MouseArea { anchors.fill: parent; z: -1; acceptedButtons: Qt.LeftButton; onClicked: body.input.forceActiveFocus() }
            }

            // date, priority, remind me
            Row {
                spacing: 8
                InfoChip {
                    id: dateChip
                    objectName: "taskDateChip"
                    glyph: Theme.icon.calendar
                    label: editor.live ? Dates.describe(editor.live.due, editor.live.hasTime, Store.minute) : ""
                    onClicked: {
                        if (editor.pickDate && editor.live)
                            editor.pickDate(dateChip, editor.live.due, editor.live.hasTime, false, (ms, hasTime) => {
                                Store.updateTask(editor.task.id, { due: ms, hasTime: hasTime })
                            })
                    }
                }
                InfoChip {
                    id: prioChip
                    objectName: "taskPriorityChip"
                    glyph: Theme.icon.flag
                    glyphColor: editor.live && editor.live.priority !== "normal" ? Theme.priorityColor(editor.live.priority) : Theme.textSecondary
                    label: editor.live ? Theme.priorityLabel(editor.live.priority) : ""
                    active: prioMenu.isOpen
                    onClicked: prioMenu.openAt(prioChip, 0, prioChip.height + 4)
                }
                InfoChip {
                    id: remindChip
                    objectName: "taskRemindChip"
                    glyph: Theme.icon.reminder
                    label: "Remind me"
                    onClicked: editor.remind()
                }
            }
            Text {
                visible: editor.hint !== ""
                text: editor.hint
                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                color: Theme.textSecondary
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
                        const id = editor.task.id
                        editor.task = null
                        editor.progress = 0
                        Store.deleteTask(id)
                        editor.finished()
                    }
                }
                Row {
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    spacing: 8
                    TextButton { label: "Close"; onClicked: editor.close() }
                    TextButton {
                        id: doneButton
                        primary: true
                        label: editor.live && editor.live.done ? "Reopen" : "Done"
                        onClicked: {
                            if (editor.task)
                                Store.toggleTask(editor.task.id)
                            editor.close()
                        }
                    }
                }
            }
        }
    }

    // used by the self-test and the demos: window coordinates of the controls
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function probe(name, index) {
        if (name === "menuItem") {
            const entry = prioMenu.itemAtIndex(index)
            return entry ? center(entry) : null
        }
        const item = ({ title: title, body: body, date: dateChip, priority: prioChip, remind: remindChip,
                        delete: deleteButton, done: doneButton, check: done, sheet: sheet })[name]
        return item ? center(item) : null
    }
    function probeOpen(name) {
        if (name === "date" && pickDate && live)
            pickDate(dateChip, live.due, live.hasTime, false, (ms, hasTime) => {})
    }
    function remind() {
        if (!task || !live)
            return
        if (live.due !== null) {
            const id = Store.remindForTask(task.id)
            const r = Store.reminder(id)
            hint = "Reminder set for " + Dates.describe(r.due, true, Store.minute)
            return
        }
        if (pickDate)
            pickDate(remindChip, null, false, true, (ms, hasTime) => {
                if (ms === null)
                    return
                Store.addReminder(live.title, ms, { link: { kind: "task", id: task.id } })
                hint = "Reminder set for " + Dates.describe(ms, true, Store.minute)
            })
    }

    ContextMenu {
        id: prioMenu
        cardWidth: 170
        model: ["urgent", "high", "normal", "low"].map(p => ({
            key: p, label: Theme.priorityLabel(p),
            glyph: editor.live && editor.live.priority === p ? Theme.icon.check : ""
        }))
        onChosen: (key) => { if (editor.task) Store.updateTask(editor.task.id, { priority: key }) }
    }
}
