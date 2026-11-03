import QtQuick

// One Keep-style note: title, wrapped preview, hover toolbar.
Item {
    id: card
    property var note
    property bool hiddenForEditor: false
    readonly property bool hovered: hover.hovered
    signal opened(Item source)

    implicitHeight: panel.height
    opacity: hiddenForEditor ? 0 : 1
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    GlassPanel {
        id: panel
        width: parent.width
        height: content.implicitHeight + Theme.pad * 2 + toolbar.height
        fill: Theme.tint(card.note.tint)
        hovered: hover.hovered
        y: hover.hovered ? -2 : 0
        Behavior on y { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }

        Column {
            id: content
            x: Theme.pad
            y: Theme.pad
            width: parent.width - Theme.pad * 2
            spacing: 6

            Text {
                width: parent.width
                visible: card.note.title !== ""
                text: card.note.title
                font.family: Theme.font
                font.pixelSize: Theme.sizeTitle
                font.weight: Font.Medium
                color: Theme.text
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }
            Text {
                width: parent.width
                visible: card.note.body !== ""
                text: card.note.body
                font.family: Theme.font
                font.pixelSize: card.note.title === "" && card.note.body.length < 70 ? Theme.sizeTitle : Theme.sizeBody
                color: card.note.title === "" ? Theme.text : Theme.textSecondary
                lineHeight: 1.2
                wrapMode: Text.Wrap
                maximumLineCount: 9
                elide: Text.ElideRight
            }
        }

        // Hover toolbar: fades in, sits in reserved space so cards never jump.
        Row {
            id: toolbar
            anchors { left: parent.left; bottom: parent.bottom; leftMargin: Theme.pad - 8; bottomMargin: 4 }
            height: 30
            spacing: 2
            opacity: hover.hovered ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            IconButton {
                glyph: card.note.pinned ? Theme.icon.pinned : Theme.icon.pin
                iconSize: 13
                onClicked: Store.updateNote(card.note.id, { pinned: !card.note.pinned })
            }
            IconButton {
                glyph: Theme.icon.palette
                iconSize: 13
                onClicked: {
                    const order = Theme.tintOrder
                    const next = order[(order.indexOf(card.note.tint) + 1) % order.length]
                    Store.updateNote(card.note.id, { tint: next })
                }
            }
            IconButton {
                glyph: Theme.icon.archive
                iconSize: 13
                onClicked: Store.updateNote(card.note.id, { archived: !card.note.archived, pinned: false })
            }
            IconButton {
                glyph: Theme.icon.trash
                iconSize: 13
                hoverIconColor: Theme.danger
                onClicked: Store.deleteNote(card.note.id)
            }
        }

        // Taps on the card body open the editor; toolbar buttons take their own taps.
        TapHandler {
            onTapped: (point) => {
                if (point.position.y < panel.height - toolbar.height - 2)
                    card.opened(panel)
            }
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
}
