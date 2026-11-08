import QtQuick
import "dates.js" as Dates

// Today's tasks plus overdue ones, tick to complete, and an inline add field.
OverlayPanel {
    id: panel
    title: "To-Do"
    meta: Store.todayDone + " / " + Store.todayTotal
    property string query: ""

    readonly property string needle: query.trim().toLowerCase()
    readonly property var shown: Store.todayTasks.filter(t => needle === "" || t.title.toLowerCase().includes(needle))

    // The checkbox of the visible row `i` (the self-test clicks it).
    function checkAt(i) { const row = list.itemAtIndex(i); return row ? row.box : null }

    ListView {
        id: list
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: addRow.top; bottomMargin: 4 }
        anchors.leftMargin: 6
        anchors.rightMargin: 6
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: panel.shown

        delegate: Rectangle {
            id: row
            required property var modelData
            readonly property bool overdue: Dates.groupOf(modelData.due, Store.minute) === "overdue" && !modelData.done
            readonly property Item box: check
            width: ListView.view.width
            height: 34
            radius: Theme.radius
            color: rowHover.hovered ? Theme.hover : "transparent"
            Behavior on color { ColorAnimation { duration: Theme.fast } }

            Checkbox {
                id: check
                anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                checked: row.modelData.done
                onToggled: Store.toggleTask(row.modelData.id)
            }
            Text {
                id: tag
                visible: row.overdue
                anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                text: "Overdue"
                font.family: Theme.font; font.pixelSize: 11
                color: Qt.rgba(0.93, 0.42, 0.44, 0.85)
            }
            Text {
                anchors {
                    left: check.right; leftMargin: 12
                    right: tag.visible ? tag.left : parent.right; rightMargin: 10
                    verticalCenter: parent.verticalCenter
                }
                text: row.modelData.title
                elide: Text.ElideRight
                font.family: Theme.font; font.pixelSize: Theme.sizeBody
                font.strikeout: row.modelData.done
                color: row.modelData.done ? Theme.textMuted : Theme.text
            }
            HoverHandler { id: rowHover }
        }

        Rectangle {
            visible: list.contentHeight > list.height
            x: list.width - 4
            y: list.visibleArea.yPosition * list.height
            width: 3; radius: 1.5
            height: list.visibleArea.heightRatio * list.height
            color: Qt.rgba(1, 1, 1, list.moving ? 0.35 : 0.15)
        }
    }

    Text {
        visible: panel.shown.length === 0
        anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: 20 }
        text: panel.needle === "" ? "All clear" : "No matches"
        font.family: Theme.font; font.pixelSize: Theme.sizeBody
        color: Theme.textMuted
    }

    Rectangle {
        id: addRow
        x: Theme.pad; width: parent.width - Theme.pad * 2
        anchors.bottom: parent.bottom; anchors.bottomMargin: 12
        height: 34
        radius: Theme.radius
        color: Qt.rgba(1, 1, 1, 0.05)
        border.width: 1
        border.color: newTask.input.activeFocus ? Theme.borderStrong : Theme.border
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
        Icon {
            id: plus
            anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
            glyph: Theme.icon.add; size: 12; color: Theme.textMuted
        }
        TextBox {
            id: newTask
            anchors { left: plus.right; leftMargin: 10; right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
            placeholder: "Add a task"
            multiline: false
            onSubmitted: { Store.addTaskFromText(newTask.text, true); newTask.text = "" }
        }
        TapHandler { onTapped: newTask.input.forceActiveFocus() }
    }
}
