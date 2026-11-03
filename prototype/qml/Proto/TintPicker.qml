import QtQuick

// Row of muted color swatches; "none" shows as an outlined empty circle.
Row {
    id: picker
    property string current: "none"
    signal picked(string tint)
    spacing: 6
    Repeater {
        model: Theme.tintOrder
        Rectangle {
            required property string modelData
            width: 20; height: 20; radius: 10
            color: modelData === "none" ? "transparent" : Qt.rgba(Theme.tint(modelData).r, Theme.tint(modelData).g, Theme.tint(modelData).b, 0.95)
            border.width: picker.current === modelData ? 2 : 1
            border.color: picker.current === modelData ? Theme.accent : Theme.borderStrong
            scale: swatchHover.hovered ? 1.12 : 1
            Behavior on scale { NumberAnimation { duration: Theme.fast } }
            Icon {
                anchors.centerIn: parent
                visible: parent.modelData === "none"
                glyph: "\uE8D8"
                size: 10
                color: Theme.textMuted
            }
            HoverHandler { id: swatchHover; cursorShape: Qt.PointingHandCursor }
            MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: picker.picked(parent.modelData) }
        }
    }
}
