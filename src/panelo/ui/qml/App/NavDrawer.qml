import QtQuick

// Hidden Keep-style navigation drawer that slides in from the left.
Item {
    id: drawer
    property bool open: false
    property string section: "notes"
    property real progress: open ? 1 : 0
    signal selected(string key)

    // Centre of a nav row in window coordinates (used by the self-test).
    function itemCenter(key) {
        for (const child of col.children)
            if (child.key === key) {
                const p = child.mapToItem(null, child.width / 2, child.height / 2)
                return { x: p.x, y: p.y }
            }
        return null
    }

    Behavior on progress { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
    visible: progress > 0.001

    Rectangle {
        anchors.fill: parent
        color: Theme.scrim
        opacity: drawer.progress * 0.7
        // blocks the click: the drawer closes and nothing underneath reacts
        ClickBlocker { anchors.fill: parent; onDismissed: drawer.open = false }
    }

    component NavItem: Rectangle {
        id: item
        property string key: ""
        property string glyph: ""
        property string label: ""
        width: parent.width
        height: 36
        radius: Theme.radius
        color: drawer.section === key ? Theme.selected : (hover.hovered ? Theme.hover : "transparent")
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Rectangle {
            visible: drawer.section === item.key
            anchors { left: parent.left; verticalCenter: parent.verticalCenter }
            width: 2; height: 16; radius: 1
            color: Theme.accent
        }
        Icon {
            id: icon
            anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
            glyph: item.glyph; size: 14
            color: drawer.section === item.key ? Theme.text : Theme.textSecondary
        }
        Text {
            anchors { left: icon.right; leftMargin: 14; verticalCenter: parent.verticalCenter }
            text: item.label
            font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
            font.weight: drawer.section === item.key ? Font.Medium : Font.Normal
            color: drawer.section === item.key ? Theme.text : Theme.textSecondary
        }
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        // A MouseArea (not a TapHandler): it accepts the press and release, so the click
        // can never also reach a note card lying behind the drawer.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            onClicked: { drawer.selected(item.key); drawer.open = false }
        }
    }

    Rectangle {
        id: panel
        width: 250
        height: parent.height
        x: -width + width * drawer.progress
        color: Theme.raised
        border.width: 1
        border.color: Theme.border

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // keep clicks inside the drawer

        Flickable {
            anchors.fill: parent
            anchors.margins: 10
            contentHeight: col.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            Column {
                id: col
                width: parent.width
                spacing: 2
                NavItem { key: "notes"; glyph: Theme.icon.note; label: "Notes" }
                NavItem { key: "tasks"; glyph: Theme.icon.tasks; label: "Tasks" }
                NavItem { key: "reminders"; glyph: Theme.icon.reminder; label: "Reminders" }
                NavItem { key: "focus"; glyph: Theme.icon.focus; label: "Focus" }
                NavItem { key: "files"; glyph: Theme.icon.folder; label: "Files" }
                Item { width: 1; height: 10 }
                Rectangle { width: parent.width; height: 1; color: Theme.border }
                Item { width: 1; height: 8 }
                NavItem { key: "archive"; glyph: Theme.icon.archive; label: "Archive" }
                NavItem { key: "trash"; glyph: Theme.icon.trash; label: "Trash" }
                NavItem { key: "settings"; glyph: Theme.icon.settings; label: "Settings" }
            }
        }
    }
}
