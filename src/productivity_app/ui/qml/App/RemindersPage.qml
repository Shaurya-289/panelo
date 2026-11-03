import QtQuick
import "dates.js" as Dates
import "recurrence.js" as Recur

// The Reminders view: a smart quick add ("Remind me to…", a time is required), then Due now
// (highlighted, with Snooze +10 min and Done), Today, Tomorrow, This week, Later, and Past
// (done reminders, collapsed by default). One-time reminders only.
Item {
    id: page
    property var pickDate: null            // pickDate(anchorItem, ms, hasTime, requireTime, callback)
    property bool pastOpen: false
    signal reminderOpened(var reminder, Item source)
    signal linkOpened(var link, Item chip)

    readonly property var groups: Store.reminderGroups
    readonly property real listW: Math.min(page.width - 48, 880)
    property alias addBar: add
    property var items: ({})               // probes for the self-test: "row:5", "snooze:1", ...

    function register(key, item) { items[key] = item }
    function unregister(key, item) { if (items[key] === item) delete items[key] }
    function centerOf(key, dx) {
        const item = items[key]
        if (!item)
            return null
        const p = item.mapToItem(null, item.width / 2 + (dx || 0), item.height / 2)
        return { x: p.x, y: p.y }
    }
    function scrollTop() { flick.contentY = 0 }

    function createFrom(p, ms) {
        Store.addReminder(p.title, ms)
        add.clear()
    }
    // A time is required: without one, Enter opens the picker (on the parsed day, if any).
    function submit(p) {
        if (p.title === "")
            return
        if (p.due !== null && p.hasTime) {
            createFrom(p, p.due)
            return
        }
        if (page.pickDate)
            page.pickDate(add, p.due, false, true, (ms) => { if (ms !== null) page.createFrom(p, ms) })
    }

    SmartAdd {
        id: add
        x: (page.width - page.listW) / 2
        y: 8
        width: page.listW
        needsTime: true
        placeholder: "Remind me to…  try “Pay rent tomorrow 9am”"
        glyph: Theme.icon.reminder
        onSubmitted: (p) => page.submit(p)
    }

    Flickable {
        id: flick
        anchors { left: parent.left; right: parent.right; top: add.bottom; topMargin: 14; bottom: parent.bottom }
        contentWidth: width
        contentHeight: column.implicitHeight + 28
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: column
            x: (flick.width - width) / 2
            width: page.listW
            spacing: 4

            // Due now: highlighted cards
            Repeater {
                model: page.groups.dueNow
                Rectangle {
                    id: due
                    required property var modelData
                    width: column.width
                    height: 84
                    radius: Theme.radius
                    color: Qt.rgba(1, 1, 1, 0.12)
                    border.width: 1
                    border.color: Theme.borderStrong
                    Component.onCompleted: { page.register("due:" + modelData.id, due) }
                    Component.onDestruction: page.unregister("due:" + modelData.id, due)

                    MouseArea {
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton
                        cursorShape: Qt.PointingHandCursor
                        onClicked: page.reminderOpened(due.modelData, due)
                    }
                    Icon {
                        id: dueBell
                        anchors { left: parent.left; leftMargin: 16; top: parent.top; topMargin: 16 }
                        glyph: Theme.icon.reminder; size: 15; color: Theme.accent
                    }
                    Text {
                        anchors { left: dueBell.right; leftMargin: 12; right: dueRepeat.visible ? dueRepeat.left : dueWhen.left; rightMargin: 10; verticalCenter: dueBell.verticalCenter }
                        text: due.modelData.title
                        elide: Text.ElideRight
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1; font.weight: Font.Medium
                        color: Theme.text
                    }
                    Icon {
                        id: dueRepeat
                        visible: due.modelData.repeat !== null
                        anchors { right: dueWhen.left; rightMargin: 6; verticalCenter: dueBell.verticalCenter }
                        glyph: Theme.icon.repeat; size: 11; color: Theme.textSecondary
                    }
                    Text {
                        id: dueWhen
                        anchors { right: parent.right; rightMargin: 16; verticalCenter: dueBell.verticalCenter }
                        text: Dates.relative(due.modelData.due, Store.minute)
                        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                        color: Theme.textSecondary
                    }
                    InfoChip {
                        id: dueLink
                        compact: true
                        visible: info !== null
                        readonly property var info: Store.linkInfo(due.modelData.link)
                        anchors { left: parent.left; leftMargin: 16; bottom: parent.bottom; bottomMargin: 10 }
                        glyph: info && info.kind === "task" ? Theme.icon.tasks : Theme.icon.note
                        label: info ? info.title : ""
                        maxLabelWidth: 200
                        dimmed: info !== null && info.gone
                        strike: dimmed
                        onClicked: if (info && !info.gone) page.linkOpened(due.modelData.link, dueLink)
                    }
                    Row {
                        anchors { right: parent.right; rightMargin: 10; bottom: parent.bottom; bottomMargin: 8 }
                        spacing: 6
                        TextButton {
                            id: snooze
                            height: 28; label: "Snooze +10 min"
                            Component.onCompleted: page.register("snooze:" + due.modelData.id, snooze)
                            Component.onDestruction: page.unregister("snooze:" + due.modelData.id, snooze)
                            onClicked: Store.snoozeReminder(due.modelData.id)
                        }
                        TextButton {
                            id: doneBtn
                            height: 28; primary: true; label: "Done"
                            Component.onCompleted: page.register("done:" + due.modelData.id, doneBtn)
                            Component.onDestruction: page.unregister("done:" + due.modelData.id, doneBtn)
                            onClicked: Store.completeReminder(due.modelData.id)
                        }
                    }
                }
            }
            Item { width: 1; height: 8; visible: page.groups.dueNow.length > 0 }

            // Today, Tomorrow, This week, Later
            Repeater {
                model: [
                    { key: "today", label: "Today" }, { key: "tomorrow", label: "Tomorrow" },
                    { key: "week", label: "This week" }, { key: "later", label: "Later" }
                ]
                Column {
                    id: group
                    required property var modelData
                    readonly property var list: page.groups[modelData.key]
                    width: column.width
                    visible: list.length > 0
                    spacing: 2
                    SectionLabel { text: group.modelData.label + "  ·  " + group.list.length; leftPadding: 4; topPadding: 6; bottomPadding: 4 }
                    Repeater { model: group.list; ReminderRow {} }
                    Item { width: 1; height: 10 }
                }
            }

            // Past (done): collapsed by default
            Column {
                width: column.width
                visible: page.groups.past.length > 0
                spacing: 2
                Rectangle {
                    id: pastHeader
                    width: parent.width; height: 30
                    radius: Theme.radius
                    color: pastMouse.containsMouse ? Theme.hover : "transparent"
                    Component.onCompleted: page.register("pastHeader", pastHeader)
                    Row {
                        anchors { left: parent.left; leftMargin: 4; verticalCenter: parent.verticalCenter }
                        spacing: 8
                        Icon {
                            anchors.verticalCenter: parent.verticalCenter
                            glyph: page.pastOpen ? Theme.icon.chevronDown : Theme.icon.chevronRight
                            size: 10; color: Theme.textMuted
                        }
                        SectionLabel { text: "Past  ·  " + page.groups.past.length }
                    }
                    MouseArea {
                        id: pastMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.LeftButton
                        cursorShape: Qt.PointingHandCursor
                        onClicked: page.pastOpen = !page.pastOpen
                    }
                }
                Column {
                    width: parent.width
                    spacing: 2
                    visible: page.pastOpen
                    Repeater { model: page.pastOpen ? page.groups.past : []; ReminderRow { past: true } }
                }
            }

            Column {
                visible: page.groups.dueNow.length === 0 && page.upcomingCount === 0 && page.groups.past.length === 0
                width: parent.width
                topPadding: 60
                spacing: 12
                Icon { anchors.horizontalCenter: parent.horizontalCenter; glyph: Theme.icon.reminder; size: 56; color: Qt.rgba(1, 1, 1, 0.12) }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "No reminders"
                    font.family: Theme.font; font.pixelSize: Theme.sizeTitle + 2; color: Theme.textMuted
                }
            }
        }
    }
    readonly property int upcomingCount: Store.upcomingReminders.length

    // One reminder row: a bell, the title with its relative time under it, an optional link chip
    // and the time on the right.
    component ReminderRow: Rectangle {
        id: row
        required property var modelData
        property bool past: false
        readonly property var info: Store.linkInfo(modelData.link)
        width: column.width
        height: 52
        radius: Theme.radius
        color: rowMouse.containsMouse ? Theme.hover : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Component.onCompleted: page.register("row:" + modelData.id, row)
        Component.onDestruction: page.unregister("row:" + modelData.id, row)

        MouseArea {
            id: rowMouse
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton
            cursorShape: Qt.PointingHandCursor
            onClicked: page.reminderOpened(row.modelData, row)
        }
        Icon {
            id: bell
            anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
            glyph: Theme.icon.reminder; size: 14
            color: row.past ? Theme.textMuted : Theme.textSecondary
        }
        Text {
            id: whenText
            anchors { right: parent.right; rightMargin: 14; verticalCenter: parent.verticalCenter }
            text: row.past ? Dates.shortDay(row.modelData.doneAt, Store.minute) + " " + Dates.clock(row.modelData.doneAt)
                           : Dates.formatDue(row.modelData.due, true, Store.minute)
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: row.past ? Theme.textMuted : Theme.textSecondary
        }
        Icon {
            id: repeatGlyph
            visible: row.modelData.repeat !== null
            anchors { right: whenText.left; rightMargin: 6; verticalCenter: parent.verticalCenter }
            glyph: Theme.icon.repeat; size: 11
            color: Theme.textSecondary
        }
        InfoChip {
            id: link
            compact: true
            visible: row.info !== null
            anchors { right: repeatGlyph.visible ? repeatGlyph.left : whenText.left; rightMargin: 14; verticalCenter: parent.verticalCenter }
            glyph: row.info && row.info.kind === "task" ? Theme.icon.tasks : Theme.icon.note
            label: row.info ? row.info.title : ""
            maxLabelWidth: 150
            dimmed: row.info !== null && row.info.gone
            strike: dimmed
            Component.onCompleted: page.register("link:" + row.modelData.id, link)
            Component.onDestruction: page.unregister("link:" + row.modelData.id, link)
            onClicked: if (row.info && !row.info.gone) page.linkOpened(row.modelData.link, link)
        }
        Column {
            anchors {
                left: bell.right; leftMargin: 14
                right: link.visible ? link.left : whenText.left; rightMargin: 12
                verticalCenter: parent.verticalCenter
            }
            Text {
                width: parent.width
                text: row.modelData.title
                elide: Text.ElideRight
                font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
                color: row.past ? Theme.textMuted : Theme.text
            }
            Text {
                width: parent.width
                text: row.past ? "Done" : Recur.subtitle(row.modelData.repeat, row.modelData.anchor || row.modelData.due,
                                                         Dates.relative(row.modelData.due, Store.minute))
                elide: Text.ElideRight
                font.family: Theme.font; font.pixelSize: 11
                color: Theme.textMuted
            }
        }
    }
}
