import QtQuick

// Keep-style pop-out editor. It grows from the clicked card's rectangle to a
// centered card, over a dim scrim; clicking outside saves and closes.
Item {
    id: editor
    property var note: null
    property rect fromRect: Qt.rect(0, 0, 0, 0)
    property real progress: 0
    property string tint: "none"
    property bool pinned: false
    readonly property bool isOpen: note !== null
    signal finished()

    visible: progress > 0.001

    function open(n, source) {
        note = n
        fromRect = source.mapToItem(editor, 0, 0, source.width, source.height)
        title.text = n.title
        body.text = n.body
        tint = n.tint
        pinned = n.pinned
        closeAnim.stop()
        openAnim.restart()
        body.input.forceActiveFocus()
        body.input.cursorPosition = body.text.length
    }
    function close() {
        if (!note || closeAnim.running) return
        openAnim.stop()
        closeAnim.restart()
    }

    NumberAnimation { id: openAnim; target: editor; property: "progress"; to: 1; duration: 260; easing.type: Easing.OutCubic }
    SequentialAnimation {
        id: closeAnim
        NumberAnimation { target: editor; property: "progress"; to: 0; duration: 200; easing.type: Easing.InOutCubic }
        ScriptAction { script: { const keep = editor.note; editor.note = null; Store.updateNote(keep.id, { title: title.text, body: body.text, tint: editor.tint, pinned: editor.pinned }); editor.finished() } }
    }

    // Scrim
    Rectangle {
        anchors.fill: parent
        color: Theme.scrim
        opacity: editor.progress
        // blocks the click: the editor closes and nothing underneath reacts
        ClickBlocker { anchors.fill: parent; onDismissed: editor.close() }
    }

    // Target geometry
    readonly property real targetW: Math.min(620, width - 80)
    readonly property real targetH: Math.min(contentCol.implicitHeight + Theme.pad * 2 + 8, height * 0.8)
    readonly property real targetX: (width - targetW) / 2
    readonly property real targetY: Math.max(40, (height - targetH) * 0.4)

    // used by the self-test
    function trashCenter() {
        const p = trashButton.mapToItem(null, trashButton.width / 2, trashButton.height / 2)
        return { x: p.x, y: p.y }
    }
    function lerp(a, b) { return a + (b - a) * progress }

    Rectangle {
        id: sheet
        x: editor.lerp(editor.fromRect.x, editor.targetX)
        y: editor.lerp(editor.fromRect.y, editor.targetY)
        width: editor.lerp(editor.fromRect.width, editor.targetW)
        height: editor.lerp(editor.fromRect.height, editor.targetH)
        radius: Theme.radius
        color: Theme.raised
        border.width: 1
        border.color: Theme.borderStrong
        clip: true

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // swallow clicks so they don't reach the scrim

        Rectangle {               // tint layer over the opaque base
            anchors.fill: parent
            radius: Theme.radius
            color: Theme.tint(editor.tint)
            Behavior on color { ColorAnimation { duration: Theme.normal } }
        }

        Column {
            id: contentCol
            x: Theme.pad + 4
            y: Theme.pad + 2
            width: editor.targetW - (Theme.pad + 4) * 2
            spacing: 12
            opacity: Math.max(0, (editor.progress - 0.5) * 2)

            Item {
                width: parent.width
                height: title.implicitHeight
                TextBox {
                    id: title
                    anchors { left: parent.left; right: pinButton.left; rightMargin: 8 }
                    placeholder: "Title"
                    pixelSize: Theme.sizeHeading
                    weight: Font.Medium
                    multiline: false
                    onSubmitted: body.input.forceActiveFocus()
                }
                IconButton {
                    id: pinButton
                    anchors.right: parent.right
                    glyph: editor.pinned ? Theme.icon.pinned : Theme.icon.pin
                    active: editor.pinned
                    onClicked: editor.pinned = !editor.pinned
                }
            }
            Flickable {
                width: parent.width
                height: Math.min(body.implicitHeight, editor.height * 0.8 - 150)
                contentHeight: body.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                TextBox {
                    id: body
                    width: parent.width
                    placeholder: "Note"
                    pixelSize: 14
                    color: Theme.text
                    onSubmitted: editor.close()
                }
            }
            Item {
                width: parent.width
                height: 34
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 14
                    TintPicker {
                        anchors.verticalCenter: parent.verticalCenter
                        current: editor.tint
                        onPicked: (t) => editor.tint = t
                    }
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        IconButton {
                            glyph: Theme.icon.archive
                            onClicked: { if (editor.note) Store.updateNote(editor.note.id, { archived: !editor.note.archived }); editor.close() }
                        }
                        IconButton {
                            id: trashButton
                            glyph: Theme.icon.trash
                            hoverIconColor: Theme.danger
                            onClicked: { const id = editor.note.id; editor.note = null; editor.progress = 0; Store.deleteNote(id); editor.finished() }
                        }
                    }
                }
                Row {
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    spacing: 10
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: editor.note ? "Edited " + editor.note.edited : ""
                        font.family: Theme.font; font.pixelSize: 11
                        color: Theme.textMuted
                    }
                    TextButton { label: "Close"; onClicked: editor.close() }
                }
            }
        }
    }
}
