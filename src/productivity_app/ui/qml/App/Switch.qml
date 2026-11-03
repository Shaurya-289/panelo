import QtQuick

// A toggle switch: a pill track and a round knob. White track when on. A MouseArea takes the
// click; the parent owns the value (bind `checked`, handle toggled()).
Rectangle {
    id: sw
    property bool checked: false
    signal toggled()

    width: 42; height: 24
    radius: height / 2
    color: checked ? Theme.accent : (hover.hovered ? Qt.rgba(1, 1, 1, 0.20) : Qt.rgba(1, 1, 1, 0.14))
    border.width: checked ? 0 : 1
    border.color: Theme.border
    Behavior on color { ColorAnimation { duration: Theme.fast } }

    Rectangle {
        width: 18; height: 18; radius: 9
        anchors.verticalCenter: parent.verticalCenter
        x: sw.checked ? parent.width - width - 3 : 3
        color: sw.checked ? Theme.accentInk : Qt.rgba(1, 1, 1, 0.75)
        Behavior on x { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: sw.toggled() }
}
