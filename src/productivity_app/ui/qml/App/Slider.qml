import QtQuick

// A thin slider with a white knob. The parent owns the value (bind `value`, handle moved(v)); a
// press or a drag anywhere on the track sets it. A MouseArea takes the events.
Item {
    id: slider
    property real from: 0
    property real to: 1
    property real value: 0
    property real stepSize: 0            // 0: continuous
    readonly property real position: to > from ? Math.max(0, Math.min(1, (value - from) / (to - from))) : 0
    readonly property bool pressed: mouse.pressed
    property alias track: trackBar
    signal moved(real value)

    implicitWidth: 220
    implicitHeight: 28

    function valueAt(fraction) {
        let v = from + Math.max(0, Math.min(1, fraction)) * (to - from)
        if (stepSize > 0)
            v = Math.round(v / stepSize) * stepSize
        return Math.max(from, Math.min(to, v))
    }

    Rectangle {
        id: trackBar
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
        anchors.leftMargin: 8; anchors.rightMargin: 8
        height: 4; radius: 2
        color: Qt.rgba(1, 1, 1, 0.14)
        Rectangle {
            width: parent.width * slider.position
            height: parent.height; radius: 2
            color: Theme.accent
        }
    }
    Rectangle {
        x: trackBar.x + trackBar.width * slider.position - width / 2
        anchors.verticalCenter: parent.verticalCenter
        width: slider.pressed ? 18 : 16; height: width; radius: width / 2
        color: Theme.accent
        border.width: 1
        border.color: Qt.rgba(0, 0, 0, 0.25)
        Behavior on width { NumberAnimation { duration: Theme.fast } }
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        hoverEnabled: true
        preventStealing: true
        cursorShape: Qt.PointingHandCursor
        function setFrom(x) {
            const v = slider.valueAt((x - trackBar.x) / trackBar.width)
            if (v !== slider.value)
                slider.moved(v)
        }
        onPressed: (event) => setFrom(event.x)
        onPositionChanged: (event) => { if (pressed) setFrom(event.x) }
    }
}
