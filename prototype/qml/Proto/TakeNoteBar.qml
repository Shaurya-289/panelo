import QtQuick

// Keep's "Take a note…" bar: collapsed single line, expands in place into a Title
// field, a multi-line Note field and a Done button. `compact` is the overlay's variant:
// slimmer, and the tint picker only shows when the panel is wide enough.
GlassPanel {
    id: bar
    property bool expanded: false
    property bool compact: false
    property string tint: "none"
    property alias titleText: title.text
    property alias bodyText: body.text
    readonly property bool showTints: !compact || width > 330
    readonly property bool iconsVisible: collapsedIcons.visible      // read by the self-test
    readonly property bool hasChecklistIcon: {
        for (let i = 0; i < collapsedIcons.children.length; i++)
            if (collapsedIcons.children[i].glyph === Theme.icon.checklist)
                return true
        return false
    }
    readonly property int iconCount: collapsedIcons.children.length

    function open() { expanded = true; body.input.forceActiveFocus() }
    // Saves when there is a title or a body (Store ignores an empty note), then collapses.
    function commit() {
        Store.addNote(title.text, body.text, tint)
        title.text = ""; body.text = ""; tint = "none"
        expanded = false
        bar.forceActiveFocus()
    }
    // Used by the overlay's self-test and demos: type a note and save it.
    function capture(noteTitle, noteBody) {
        expanded = true
        title.text = noteTitle
        body.text = noteBody
        commit()
    }

    fill: expanded ? Theme.tint(tint) : Theme.panel
    hovered: hover.hovered || expanded
    height: expanded ? expandedColumn.implicitHeight + Theme.pad * 2 : (compact ? 36 : 46)
    clip: true
    Behavior on height { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }

    Keys.onEscapePressed: commit()

    // Collapsed state
    Item {
        anchors.fill: parent
        visible: !bar.expanded
        Icon {
            id: plus
            visible: bar.compact
            anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
            glyph: Theme.icon.add; size: 12; color: Theme.textMuted
        }
        Text {
            anchors {
                left: bar.compact ? plus.right : parent.left
                leftMargin: bar.compact ? 10 : Theme.pad + 2
                verticalCenter: parent.verticalCenter
            }
            text: "Take a note…"
            font.family: Theme.font
            font.pixelSize: bar.compact ? Theme.sizeBody : Theme.sizeTitle
            color: Theme.textMuted
        }
        // Notes are plain text: only the palette is offered here
        Row {
            id: collapsedIcons
            visible: !bar.compact
            anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
            IconButton { glyph: Theme.icon.palette; onClicked: bar.open() }
        }
        TapHandler { onTapped: bar.open() }
        HoverHandler { id: hover; cursorShape: Qt.IBeamCursor }
    }

    // Expanded state
    Column {
        id: expandedColumn
        visible: bar.expanded
        opacity: bar.expanded ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
        x: Theme.pad + 2
        y: bar.compact ? 10 : Theme.pad
        width: parent.width - Theme.pad * 2 - 4
        spacing: bar.compact ? 8 : 10
        TextBox {
            id: title
            width: parent.width
            placeholder: "Title"
            pixelSize: Theme.sizeTitle
            weight: Font.Medium
            multiline: false
            onSubmitted: body.input.forceActiveFocus()
        }
        Item {
            width: parent.width
            height: Math.max(body.implicitHeight, bar.compact ? 76 : 0)
            TextBox {
                id: body
                width: parent.width
                placeholder: "Take a note…"
                color: Theme.textSecondary
                onSubmitted: bar.commit()
            }
            TapHandler { onTapped: body.input.forceActiveFocus() }
        }
        Item {
            width: parent.width
            height: 30
            TintPicker {
                visible: bar.showTints
                anchors.verticalCenter: parent.verticalCenter
                current: bar.tint
                onPicked: (t) => bar.tint = t
            }
            TextButton {
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                label: "Done"
                onClicked: bar.commit()
            }
        }
    }
}
