import QtQuick

// Today's tasks with progress and an inline add field.
GlassPanel {
    id: card
    implicitHeight: column.implicitHeight + Theme.pad * 2

    Column {
        id: column
        x: Theme.pad; y: Theme.pad
        width: parent.width - Theme.pad * 2
        spacing: 8

        Item {
            width: parent.width; height: 16
            SectionLabel { text: "Today"; anchors.verticalCenter: parent.verticalCenter }
            Text {
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                text: Store.todayDone + " / " + Store.todayTotal
                font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                color: Theme.textMuted
            }
        }
        Rectangle {
            width: parent.width; height: 2; radius: 1
            color: Qt.rgba(1, 1, 1, 0.12)
            Rectangle {
                width: Store.todayTotal ? parent.width * Store.todayDone / Store.todayTotal : 0
                height: parent.height; radius: 1
                color: Theme.accent
                Behavior on width { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
            }
        }
        Column {
            width: parent.width
            spacing: 0
            Repeater {
                // open tasks first, then done
                model: Store.todayTasks
                Rectangle {
                    id: row
                    required property var modelData
                    width: parent.width; height: 32
                    radius: Theme.radius
                    color: rowHover.hovered ? Theme.hover : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                    Checkbox {
                        id: check
                        anchors { left: parent.left; leftMargin: 6; verticalCenter: parent.verticalCenter }
                        checked: row.modelData.done
                        onToggled: Store.toggleTask(row.modelData.id)
                    }
                    Text {
                        anchors { left: check.right; leftMargin: 10; right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
                        text: row.modelData.title
                        elide: Text.ElideRight
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody
                        font.strikeout: row.modelData.done
                        color: row.modelData.done ? Theme.textMuted : Theme.text
                    }
                    HoverHandler { id: rowHover }
                }
            }
        }
        Item {
            width: parent.width; height: 30
            Icon {
                id: plus
                anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                glyph: Theme.icon.add; size: 12; color: Theme.textMuted
            }
            TextBox {
                id: newTask
                anchors { left: plus.right; leftMargin: 12; right: parent.right; verticalCenter: parent.verticalCenter }
                placeholder: "Add a task"
                multiline: false
                onSubmitted: { Store.addTaskFromText(newTask.text, true); newTask.text = "" }
            }
        }
    }
}
