import QtQuick
import "dates.js" as Dates
import "recurrence.js" as Recur

// Due-now reminders (highlighted, with Snooze / Done), then upcoming ones by time,
// and an add row whose clock button opens the time picker.
OverlayPanel {
    id: panel
    title: "Reminders"
    property string query: ""
    property alias clockButton: clock
    property alias draft: titleBox.text    // text of the new-reminder field
    signal pickerRequested(Item anchor)

    readonly property string needle: query.trim().toLowerCase()
    readonly property var dueNow: Store.reminderGroups.dueNow.filter(r => needle === "" || r.title.toLowerCase().includes(needle))
    readonly property var upcoming: Store.upcomingReminders.filter(r => needle === "" || r.title.toLowerCase().includes(needle))

    function addWhen(ms) {
        Store.addReminder(titleBox.text, ms)
        titleBox.text = ""
    }

    Flickable {
        id: scroller
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: addRow.top; bottomMargin: 6 }
        contentHeight: column.implicitHeight + 6
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: column
            x: Theme.pad
            width: parent.width - Theme.pad * 2
            spacing: 6

            Repeater {
                model: panel.dueNow
                Rectangle {
                    id: due
                    required property var modelData
                    width: column.width
                    height: 72
                    radius: Theme.radius
                    color: Qt.rgba(1, 1, 1, 0.12)
                    border.width: 1
                    border.color: Theme.borderStrong

                    Icon {
                        id: bell
                        anchors { left: parent.left; leftMargin: 10; top: parent.top; topMargin: 12 }
                        glyph: Theme.icon.reminder; size: 14; color: Theme.accent
                    }
                    Text {
                        id: dueTitle
                        anchors { left: bell.right; leftMargin: 10; right: dueRepeat.visible ? dueRepeat.left : dueTime.left; rightMargin: 8; verticalCenter: bell.verticalCenter }
                        text: due.modelData.title
                        elide: Text.ElideRight
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody; font.weight: Font.Medium
                        color: Theme.text
                    }
                    Icon {
                        id: dueRepeat
                        visible: due.modelData.repeat !== null
                        anchors { right: dueTime.left; rightMargin: 5; verticalCenter: bell.verticalCenter }
                        glyph: Theme.icon.repeat; size: 11; color: Theme.textSecondary
                    }
                    Text {
                        id: dueTime
                        anchors { right: parent.right; rightMargin: 10; verticalCenter: bell.verticalCenter }
                        text: Dates.relative(due.modelData.due, Store.minute)
                        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                        color: Theme.textSecondary
                    }
                    Row {
                        anchors { right: parent.right; rightMargin: 6; bottom: parent.bottom; bottomMargin: 6 }
                        spacing: 4
                        TextButton { height: 28; label: "Snooze +10 min"; onClicked: Store.snoozeReminder(due.modelData.id) }
                        TextButton { height: 28; primary: true; label: "Done"; onClicked: Store.completeReminder(due.modelData.id) }
                    }
                }
            }

            SectionLabel {
                visible: panel.upcoming.length > 0
                text: "Upcoming"
                topPadding: panel.dueNow.length > 0 ? 6 : 0
                leftPadding: 2
            }
            Repeater {
                model: panel.upcoming
                Rectangle {
                    id: row
                    required property var modelData
                    width: column.width
                    height: 40
                    radius: Theme.radius
                    color: rowHover.hovered ? Theme.hover : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                    Icon {
                        id: rowBell
                        anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                        glyph: Theme.icon.reminder; size: 13
                    }
                    Column {
                        anchors { left: rowBell.right; leftMargin: 12; right: rowRepeat.visible ? rowRepeat.left : rowTime.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
                        Text {
                            width: parent.width
                            text: row.modelData.title; elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody
                            color: Theme.text
                        }
                        Text {
                            width: parent.width
                            text: Recur.subtitle(row.modelData.repeat, row.modelData.anchor || row.modelData.due,
                                                 Dates.relative(row.modelData.due, Store.minute))
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: 11
                            color: Theme.textMuted
                        }
                    }
                    Icon {
                        id: rowRepeat
                        visible: row.modelData.repeat !== null
                        anchors { right: rowTime.left; rightMargin: 5; verticalCenter: parent.verticalCenter }
                        glyph: Theme.icon.repeat; size: 11; color: Theme.textSecondary
                    }
                    Text {
                        id: rowTime
                        anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
                        text: Dates.formatWhen(row.modelData.due, Store.minute)
                        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                        color: Theme.textSecondary
                    }
                    HoverHandler { id: rowHover }
                }
            }

            Text {
                visible: panel.dueNow.length === 0 && panel.upcoming.length === 0
                topPadding: 14
                anchors.horizontalCenter: parent.horizontalCenter
                text: panel.needle === "" ? "Nothing coming up" : "No matches"
                font.family: Theme.font; font.pixelSize: Theme.sizeBody
                color: Theme.textMuted
            }
        }
    }

    Item {
        id: addRow
        x: Theme.pad; width: parent.width - Theme.pad * 2
        anchors.bottom: parent.bottom; anchors.bottomMargin: 12
        height: 34

        Rectangle {
            anchors { left: parent.left; right: clock.left; rightMargin: 6; top: parent.top; bottom: parent.bottom }
            radius: Theme.radius
            color: Qt.rgba(1, 1, 1, 0.05)
            border.width: 1
            border.color: titleBox.input.activeFocus ? Theme.borderStrong : Theme.border
            Behavior on border.color { ColorAnimation { duration: Theme.fast } }
            TextBox {
                id: titleBox
                anchors { left: parent.left; leftMargin: 12; right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                placeholder: "Remind me to…"
                multiline: false
                onSubmitted: if (titleBox.text.trim() !== "") panel.pickerRequested(clock)
            }
            TapHandler { onTapped: titleBox.input.forceActiveFocus() }
        }
        IconButton {
            id: clock
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            width: 34; height: 34
            glyph: Theme.icon.clock; iconSize: 14
            onClicked: panel.pickerRequested(clock)
        }
    }
}
