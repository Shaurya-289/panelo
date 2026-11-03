import QtQuick

Rectangle {
    id: button
    property string glyph: ""
    property int iconSize: 14
    property bool active: false
    property color iconColor: active ? Theme.text : Theme.textSecondary
    property color hoverColor: Theme.hover
    property color hoverIconColor: Theme.text
    signal clicked()

    implicitWidth: 30
    implicitHeight: 30
    radius: Theme.radius
    color: tap.pressed ? Theme.pressed : (hover.hovered || active ? hoverColor : "transparent")
    Behavior on color { ColorAnimation { duration: Theme.fast } }

    Icon {
        anchors.centerIn: parent
        glyph: button.glyph
        size: button.iconSize
        color: hover.hovered ? button.hoverIconColor : button.iconColor
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    HoverHandler {
        id: hover
        cursorShape: Qt.PointingHandCursor
        onHoveredChanged: if (!hovered) hintDelay.elapsed = false
    }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; id: tap; onClicked: button.clicked() }

    // Optional tooltip-style hint above the button after a short hover ("Reset session").
    property string hint: ""
    readonly property bool hintShown: hint !== "" && hover.hovered && hintDelay.elapsed
    Timer {
        id: hintDelay
        property bool elapsed: false
        interval: 450
        running: hover.hovered && button.hint !== ""
        onTriggered: elapsed = true
    }
    Rectangle {
        visible: button.hintShown
        z: 50
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.top; bottomMargin: 4 }
        width: hintText.implicitWidth + 16; height: 22
        radius: 4
        color: Qt.rgba(0.11, 0.115, 0.135, 1)
        border.width: 1; border.color: Theme.border
        Text {
            id: hintText
            anchors.centerIn: parent
            text: button.hint
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: Theme.textSecondary
        }
    }
}
