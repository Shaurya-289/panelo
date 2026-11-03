import QtQuick

// A layer that stops every press, release, wheel and hover from reaching the items below it.
// Use it for scrims and "click outside to close" layers: pointer handlers (TapHandler,
// HoverHandler) never accept an event, so on their own the click would also land on whatever
// lies underneath. dismissed() fires on press, or on click when dismissOnPress is false.
// The matching release is swallowed too, because the press is grabbed by this area.
MouseArea {
    id: blocker
    property bool dismissOnPress: true
    signal dismissed()

    acceptedButtons: Qt.AllButtons
    hoverEnabled: true
    onPressed: (mouse) => {
        mouse.accepted = true
        if (blocker.dismissOnPress)
            blocker.dismissed()
    }
    onClicked: (mouse) => {
        if (!blocker.dismissOnPress)
            blocker.dismissed()
    }
    onWheel: (wheel) => { wheel.accepted = true }
}
