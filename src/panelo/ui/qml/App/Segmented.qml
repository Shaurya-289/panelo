import QtQuick

// A segmented control: a row of options, one selected. options: [{ key, label }].
Rectangle {
    id: seg
    property var options: []
    property string current: ""
    signal picked(string key)

    implicitWidth: row.implicitWidth + 4
    implicitHeight: 32
    radius: Theme.radius
    color: Qt.rgba(1, 1, 1, 0.05)
    border.width: 1
    border.color: Theme.border

    function center(key) {
        const index = options.findIndex(o => o.key === key)
        const item = index < 0 ? null : repeater.itemAt(index)
        if (!item)
            return null
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }

    Row {
        id: row
        x: 2; y: 2
        spacing: 0
        Repeater {
            id: repeater
            model: seg.options
            Rectangle {
                id: segment
                required property var modelData
                readonly property bool active: seg.current === modelData.key
                width: label.implicitWidth + 28
                height: seg.height - 4
                radius: Theme.radius - 1
                color: active ? Theme.accent : (tap.pressed ? Theme.pressed : hover.hovered ? Theme.hover : "transparent")
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                Text {
                    id: label
                    anchors.centerIn: parent
                    text: segment.modelData.label
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody
                    font.weight: segment.active ? Font.Medium : Font.Normal
                    color: segment.active ? Theme.accentInk : Theme.text
                }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                MouseArea { id: tap; anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: seg.picked(segment.modelData.key) }
            }
        }
    }
}
