import QtQuick

Rectangle {
    id: button
    property string label: ""
    property bool primary: false
    property color labelColor: primary ? Theme.accentInk : Theme.text
    signal clicked()
    implicitWidth: text.implicitWidth + 24
    implicitHeight: 30
    radius: Theme.radius
    color: primary ? (hover.hovered ? "#e6e7ea" : Theme.accent)
                   : (tap.pressed ? Theme.pressed : hover.hovered ? Theme.hover : "transparent")
    Behavior on color { ColorAnimation { duration: Theme.fast } }
    Text {
        id: text
        anchors.centerIn: parent
        text: button.label
        font.family: Theme.font
        font.pixelSize: Theme.sizeBody
        font.weight: Font.Medium
        color: button.labelColor
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; id: tap; onClicked: button.clicked() }
}
