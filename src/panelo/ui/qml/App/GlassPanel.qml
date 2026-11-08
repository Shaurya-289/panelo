import QtQuick

// Translucent card with a soft border and a faint top edge highlight.
Rectangle {
    id: panel
    property color fill: Theme.panel
    property bool hovered: false
    color: fill
    radius: Theme.radius
    border.width: 1
    border.color: hovered ? Theme.borderStrong : Theme.border
    Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    Behavior on color { ColorAnimation { duration: Theme.normal } }

    Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
        anchors.leftMargin: Theme.radius
        anchors.rightMargin: Theme.radius
        height: 1
        color: Theme.edgeHighlight
    }
}
