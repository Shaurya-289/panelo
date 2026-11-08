import QtQuick

// Inline name editor for a file or folder: Enter saves, Esc cancels (the browser
// handles Esc), losing focus saves. begin() fills it and selects the base name.
Rectangle {
    id: field
    property bool done: false
    signal accepted(string text)
    signal cancelled()

    implicitHeight: 26
    radius: 4
    color: Qt.rgba(1, 1, 1, 0.09)
    border.width: 1
    border.color: Theme.borderStrong

    function begin(name, isDir) {
        done = false
        box.text = name
        box.input.forceActiveFocus()
        const dot = isDir ? -1 : name.lastIndexOf(".")
        box.input.select(0, dot > 0 ? dot : name.length)
    }
    function finish() {
        if (done)
            return
        done = true
        accepted(box.text)
    }

    TextBox {
        id: box
        anchors { left: parent.left; leftMargin: 8; right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
        multiline: false
        onSubmitted: field.finish()
    }
    Connections {
        target: box.input
        function onActiveFocusChanged() {
            if (!box.input.activeFocus && field.visible)
                field.finish()
        }
    }
}
