import QtQuick

// Drag handle that lives in a gap between panels. `horizontal: true` is a vertical
// bar dragged left/right; `false` is a horizontal bar dragged up/down.
// dragged(total) reports the distance moved since the press, in pixels.
Item {
    id: splitter
    property bool horizontal: true
    readonly property bool active: hover.hovered || mouse.pressed
    readonly property bool pressed: mouse.pressed
    signal pressedDown()
    signal dragged(real total)

    Rectangle {
        anchors.centerIn: parent
        width: splitter.horizontal ? 2 : Math.min(parent.width - 16, 56)
        height: splitter.horizontal ? Math.min(parent.height - 16, 56) : 2
        radius: 1
        color: Qt.rgba(1, 1, 1, 0.30)
        opacity: splitter.active ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
    }

    HoverHandler { id: hover }
    MouseArea {
        id: mouse
        anchors.fill: parent
        cursorShape: splitter.horizontal ? Qt.SizeHorCursor : Qt.SizeVerCursor
        property point origin
        onPressed: (event) => {
            origin = mapToItem(null, event.x, event.y)
            splitter.pressedDown()
        }
        onPositionChanged: (event) => {
            if (!pressed)
                return
            const now = mapToItem(null, event.x, event.y)
            splitter.dragged(splitter.horizontal ? now.x - origin.x : now.y - origin.y)
        }
    }
}
