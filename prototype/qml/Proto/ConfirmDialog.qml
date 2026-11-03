import QtQuick

// A small modal "Are you sure?" dialog. Fill the window with it; ask(...) opens it. Its scrim
// blocks every click (a click outside cancels), and the buttons are MouseArea-based.
Item {
    id: dialog
    property bool isOpen: false
    property string title: ""
    property string message: ""
    property string confirmLabel: "Confirm"
    property var callback: null
    property real shown: isOpen ? 1 : 0
    property alias card: card
    signal cancelled()

    Behavior on shown { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    visible: shown > 0.001

    function ask(heading, text, label, onConfirm) {
        title = heading
        message = text
        confirmLabel = label || "Confirm"
        callback = onConfirm
        isOpen = true
    }
    function close() { isOpen = false }
    function confirm() {
        const cb = callback
        isOpen = false
        callback = null
        if (cb)
            cb()
    }
    function cancel() {
        if (!isOpen)
            return
        isOpen = false
        callback = null
        cancelled()
    }
    function handleEscape() {
        if (!isOpen)
            return false
        cancel()
        return true
    }
    // used by the self-test
    function confirmCenter() {
        const p = confirmButton.mapToItem(null, confirmButton.width / 2, confirmButton.height / 2)
        return { x: p.x, y: p.y }
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.scrim
        opacity: dialog.shown
        ClickBlocker { anchors.fill: parent; onDismissed: dialog.cancel() }
    }

    Rectangle {
        id: card
        width: Math.min(400, dialog.width - 48)
        height: column.implicitHeight + 40
        x: (dialog.width - width) / 2
        y: (dialog.height - height) * 0.4
        radius: Theme.radius
        color: Qt.rgba(0.11, 0.115, 0.135, 1)
        border.width: 1
        border.color: Theme.borderStrong
        opacity: dialog.shown
        scale: 0.96 + 0.04 * dialog.shown

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }

        Column {
            id: column
            x: 20; y: 20
            width: parent.width - 40
            spacing: 10
            Text {
                width: parent.width
                text: dialog.title
                wrapMode: Text.Wrap
                font.family: Theme.font; font.pixelSize: Theme.sizeTitle + 1; font.weight: Font.Medium
                color: Theme.text
            }
            Text {
                width: parent.width
                visible: dialog.message !== ""
                text: dialog.message
                wrapMode: Text.Wrap
                font.family: Theme.font; font.pixelSize: Theme.sizeBody
                color: Theme.textSecondary
            }
            Item { width: 1; height: 6 }
            Row {
                anchors.right: parent.right
                spacing: 8
                TextButton { label: "Cancel"; onClicked: dialog.cancel() }
                TextButton { id: confirmButton; primary: true; label: dialog.confirmLabel; onClicked: dialog.confirm() }
            }
        }
    }
}
