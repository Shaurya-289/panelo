import QtQuick

// A small rounded chip with a glyph and a label (the date, priority and link chips of the
// pop-out editors). A MouseArea takes the click, so it stays on the chip.
Rectangle {
    id: chip
    property string glyph: ""
    property string label: ""
    property color glyphColor: Theme.textSecondary
    property color labelColor: Theme.text
    property bool active: false
    property bool strike: false
    property bool dimmed: false
    property bool compact: false
    property real maxLabelWidth: 1000
    signal clicked()

    implicitWidth: row.implicitWidth + (compact ? 14 : 20)
    implicitHeight: compact ? 24 : 30
    radius: Theme.radius
    color: tap.pressed ? Theme.pressed : (hover.hovered || active ? Theme.selected : Qt.rgba(1, 1, 1, 0.06))
    border.width: 1
    border.color: active ? Theme.borderStrong : Theme.border
    opacity: dimmed ? 0.5 : 1
    Behavior on color { ColorAnimation { duration: Theme.fast } }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: chip.compact ? 5 : 7
        Icon {
            anchors.verticalCenter: parent.verticalCenter
            visible: chip.glyph !== ""
            glyph: chip.glyph; size: chip.compact ? 11 : 12; color: chip.glyphColor
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: chip.label
            width: Math.min(implicitWidth, chip.maxLabelWidth)
            elide: Text.ElideRight
            font.family: Theme.font; font.pixelSize: chip.compact ? Theme.sizeSmall : Theme.sizeBody
            font.strikeout: chip.strike
            color: chip.labelColor
        }
    }
    HoverHandler { id: hover; cursorShape: chip.dimmed ? Qt.ArrowCursor : Qt.PointingHandCursor }
    MouseArea { id: tap; anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: chip.clicked() }
}
