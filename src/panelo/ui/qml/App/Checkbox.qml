import QtQuick

Rectangle {
    id: box
    property bool checked: false
    signal toggled()
    width: 16; height: 16
    radius: 4
    color: checked ? Theme.accent : "transparent"
    border.width: checked ? 0 : 1.5
    border.color: hover.hovered ? Theme.text : Theme.textMuted
    Behavior on color { ColorAnimation { duration: Theme.fast } }
    Icon {
        anchors.centerIn: parent
        glyph: Theme.icon.check
        size: 10
        color: Theme.accentInk
        opacity: box.checked ? 1 : 0
        scale: box.checked ? 1 : 0.5
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        Behavior on scale { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutBack } }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: box.toggled() }
}
