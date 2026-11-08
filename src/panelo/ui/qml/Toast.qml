import QtQuick
import QtQuick.Window
import App

// Glass alert for a due reminder, shown bottom-right while the overlay is closed.
// the shell positions the window; present() slides it up and starts the 8 s timer
// (paused while hovered). The window is transparent and 16 px taller than the card
// so the card can slide up inside it.
Window {
    id: toast
    width: 340
    height: 112
    visible: false
    title: "Reminder"
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus

    property var reminderId: -1
    property string reminderTitle: ""
    property string reminderTime: ""
    property string kind: "reminder"     // "reminder" (Snooze / Done) or "focus" (Pomodoro phase over)
    property real progress: 0            // slide / fade, 0..1
    property real remaining: 1           // auto-dismiss countdown, 1..0
    readonly property int lifetime: 8000

    signal shotSaved(string path)

    function present(id, title, time, kindName) {
        reminderId = id
        reminderTitle = title
        reminderTime = time
        kind = kindName === undefined ? "reminder" : kindName
        remaining = 1
        hideAnim.stop()
        visible = true
        showAnim.restart()
        countdown.restart()
    }
    function dismiss() {
        if (!visible)
            return
        countdown.stop()
        showAnim.stop()
        hideAnim.restart()
    }
    function saveShot(path) {
        contentItem.grabToImage(result => { result.saveToFile(path); toast.shotSaved(path) })
    }

    NumberAnimation { id: showAnim; target: toast; property: "progress"; to: 1; duration: 240; easing.type: Easing.OutCubic }
    SequentialAnimation {
        id: hideAnim
        NumberAnimation { target: toast; property: "progress"; to: 0; duration: 160; easing.type: Easing.OutCubic }
        ScriptAction { script: toast.visible = false }
    }
    // remaining runs 1 -> 0 over `lifetime`, restarted from the current value after a hover pause
    NumberAnimation {
        id: countdown
        target: toast; property: "remaining"
        to: 0
        duration: toast.lifetime
        paused: hover.hovered
        onFinished: toast.dismiss()
    }

    Rectangle {
        id: card
        width: 340; height: 96
        y: 16 * (1 - toast.progress)
        opacity: toast.progress
        radius: Theme.radius
        color: Theme.raised
        border.width: 1
        border.color: Theme.borderStrong
        clip: true

        HoverHandler { id: hover }

        Icon {
            id: bell
            anchors { left: parent.left; leftMargin: 16; top: parent.top; topMargin: 16 }
            glyph: toast.kind === "focus" ? Theme.icon.focus : Theme.icon.reminder; size: 16; color: Theme.accent
        }
        Text {
            anchors { left: bell.right; leftMargin: 12; right: parent.right; rightMargin: 16; top: parent.top; topMargin: 14 }
            text: toast.reminderTitle
            elide: Text.ElideRight
            font.family: Theme.font; font.weight: Font.Medium
            font.pixelSize: toast.kind === "focus" ? Theme.sizeBody : Theme.sizeTitle   // phase messages are long
            color: Theme.text
        }
        Text {
            anchors { left: bell.right; leftMargin: 12; right: parent.right; rightMargin: 16; top: parent.top; topMargin: 36 }
            text: toast.reminderTime
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: Theme.textMuted
        }
        Row {
            anchors { right: parent.right; rightMargin: 10; bottom: parent.bottom; bottomMargin: 10 }
            spacing: 6
            TextButton {
                visible: toast.kind === "reminder"
                height: 28; label: "Snooze"
                onClicked: { Store.snoozeReminder(toast.reminderId); toast.dismiss() }
            }
            TextButton {
                visible: toast.kind === "reminder"
                height: 28; primary: true; label: "Done"
                onClicked: { Store.completeReminder(toast.reminderId); toast.dismiss() }
            }
            TextButton {
                visible: toast.kind === "focus"
                height: 28; primary: true; label: "OK"
                onClicked: toast.dismiss()
            }
        }
        // time left before auto-dismiss
        Rectangle {
            anchors { left: parent.left; bottom: parent.bottom }
            height: 2
            width: parent.width * toast.remaining
            color: Qt.rgba(1, 1, 1, 0.30)
        }
    }
}
