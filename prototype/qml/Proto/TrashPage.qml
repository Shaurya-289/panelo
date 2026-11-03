import QtQuick

// The Trash: deleted notes and tasks, newest first, kept for 30 days. Restore puts an item back
// where it was; Delete forever removes it; Empty trash (confirmed by the window) clears it all.
Item {
    id: page
    signal emptyRequested()

    readonly property var items: Store.trashItems
    readonly property real listW: Math.min(page.width - 48, 880)
    property var registry: ({})        // probes for the self-test

    function register(key, item) { registry[key] = item }
    function unregister(key, item) { if (registry[key] === item) delete registry[key] }
    function centerOf(key) {
        const item = registry[key]
        if (!item)
            return null
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }

    // header: the 30-day note and Empty trash
    Item {
        id: header
        x: (page.width - page.listW) / 2
        y: 8
        width: page.listW
        height: 40
        Text {
            anchors { left: parent.left; leftMargin: 4; verticalCenter: parent.verticalCenter }
            text: "Items are deleted forever after " + Store.trashDays + " days"
            font.family: Theme.font; font.pixelSize: Theme.sizeBody
            color: Theme.textSecondary
        }
        TextButton {
            id: emptyButton
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            label: "Empty trash"
            opacity: page.items.length > 0 ? 1 : 0.4
            enabled: page.items.length > 0
            Component.onCompleted: page.register("empty", emptyButton)
            onClicked: page.emptyRequested()
        }
    }

    Flickable {
        id: flick
        anchors { left: parent.left; right: parent.right; top: header.bottom; topMargin: 6; bottom: parent.bottom }
        contentWidth: width
        contentHeight: list.implicitHeight + 24
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        visible: page.items.length > 0

        Column {
            id: list
            x: (flick.width - width) / 2
            width: page.listW
            spacing: 4
            Repeater {
                model: page.items
                Rectangle {
                    id: row
                    required property var modelData
                    required property int index
                    width: list.width
                    height: 58
                    radius: Theme.radius
                    color: hover.hovered ? Theme.hover : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                    HoverHandler { id: hover }

                    Rectangle {
                        id: badge
                        anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
                        width: 32; height: 32; radius: Theme.radius
                        color: Qt.rgba(1, 1, 1, 0.07)
                        Icon {
                            anchors.centerIn: parent
                            glyph: row.modelData.kind === "note" ? Theme.icon.note : Theme.icon.tasks
                            size: 14; color: Theme.textSecondary
                        }
                    }
                    Row {
                        id: buttons
                        anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                        spacing: 6
                        TextButton {
                            id: restore
                            label: "Restore"
                            Component.onCompleted: page.register("restore:" + row.index, restore)
                            Component.onDestruction: page.unregister("restore:" + row.index, restore)
                            onClicked: Store.restoreItem(row.modelData.kind, row.modelData.id)
                        }
                        TextButton {
                            id: forever
                            label: "Delete forever"
                            labelColor: Theme.danger
                            Component.onCompleted: page.register("forever:" + row.index, forever)
                            Component.onDestruction: page.unregister("forever:" + row.index, forever)
                            onClicked: Store.deleteForever(row.modelData.kind, row.modelData.id)
                        }
                    }
                    Text {
                        id: left
                        anchors { right: buttons.left; rightMargin: 16; verticalCenter: parent.verticalCenter }
                        text: row.modelData.daysLeft + (row.modelData.daysLeft === 1 ? " day left" : " days left")
                        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                        color: row.modelData.daysLeft <= 3 ? Qt.rgba(0.93, 0.42, 0.44, 0.9) : Theme.textMuted
                    }
                    Column {
                        anchors { left: badge.right; leftMargin: 14; right: left.left; rightMargin: 12; verticalCenter: parent.verticalCenter }
                        spacing: 1
                        Text {
                            width: parent.width
                            text: row.modelData.title
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
                            color: Theme.text
                        }
                        Text {
                            text: row.modelData.kind === "note" ? "Note" : "Task"
                            font.family: Theme.font; font.pixelSize: 11
                            color: Theme.textMuted
                        }
                    }
                }
            }
        }
    }

    // empty state
    Column {
        visible: page.items.length === 0
        anchors.centerIn: parent
        spacing: 14
        Icon {
            anchors.horizontalCenter: parent.horizontalCenter
            glyph: Theme.icon.trash; size: 80; color: Qt.rgba(1, 1, 1, 0.12)
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Trash is empty"
            font.family: Theme.font; font.pixelSize: Theme.sizeTitle + 2
            color: Theme.textMuted
        }
    }
}
