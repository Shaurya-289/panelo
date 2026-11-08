import QtQuick

// Borderless multi- or single-line text input with a placeholder.
Item {
    id: root
    property alias text: edit.text
    property alias input: edit
    property string placeholder: ""
    property int pixelSize: Theme.sizeBody
    property int weight: Font.Normal
    property color color: Theme.text
    property bool multiline: true
    signal submitted()

    implicitHeight: Math.max(edit.contentHeight, placeholderText.implicitHeight) + 4

    TextEdit {
        id: edit
        width: parent.width
        font.family: Theme.font
        font.pixelSize: root.pixelSize
        font.weight: root.weight
        color: root.color
        wrapMode: TextEdit.Wrap
        selectByMouse: true
        selectionColor: Qt.rgba(1, 1, 1, 0.25)
        selectedTextColor: Theme.text
        Keys.onReturnPressed: (event) => {
            if (!root.multiline || (event.modifiers & Qt.ControlModifier)) root.submitted()
            else event.accepted = false
        }
    }
    Text {
        id: placeholderText
        visible: edit.text === "" && !edit.preeditText
        text: root.placeholder
        font: edit.font
        color: Theme.textMuted
    }
}
