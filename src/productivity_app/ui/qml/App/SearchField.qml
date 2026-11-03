import QtQuick

// Slim "Search everything…" field with a search glyph and a clear button.
GlassPanel {
    id: field
    property alias text: box.text
    property alias input: box.input
    implicitWidth: 440
    implicitHeight: 40
    fill: Qt.rgba(0.11, 0.115, 0.135, 0.72)
    hovered: box.input.activeFocus

    Icon {
        id: glyph
        anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
        glyph: Theme.icon.search; size: 13; color: Theme.textSecondary
    }
    TextBox {
        id: box
        anchors { left: glyph.right; leftMargin: 12; right: clear.left; rightMargin: 6; verticalCenter: parent.verticalCenter }
        placeholder: "Search everything…"
        pixelSize: Theme.sizeTitle
        multiline: false
    }
    IconButton {
        id: clear
        anchors { right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
        width: 28; height: 28
        glyph: Theme.icon.close; iconSize: 10
        opacity: box.text !== "" ? 1 : 0
        enabled: box.text !== ""
        onClicked: { box.text = ""; box.input.forceActiveFocus() }
    }
    TapHandler { onTapped: box.input.forceActiveFocus() }
}
