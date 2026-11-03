import QtQuick

// Compact notes list: quick "Take a note…" field on top, pinned notes first.
OverlayPanel {
    id: panel
    title: "Notes"
    property string query: ""
    signal noteOpened(var note, Item source)
    signal createRequested(string text)

    readonly property string needle: query.trim().toLowerCase()
    readonly property var shown: {
        const list = Store.notes.filter(n => !n.deletedAt && !n.archived
            && (needle === "" || (n.title + "\n" + n.body).toLowerCase().includes(needle)))
        return list.filter(n => n.pinned).concat(list.filter(n => !n.pinned))
    }

    // The visible row item at `i` (falls back to the panel), used as the editor's source rect.
    function rowAt(i) { return list.itemAtIndex(i) || panel }

    function lines(text) {
        return text.split("\n").map(l => l.trim().replace(/^[-•]\s*/, "")).filter(l => l !== "")
    }

    // Quick capture: collapsed "Take a note…", expands into Title + Note
    TakeNoteBar {
        id: quickAdd
        compact: true
        x: Theme.pad; width: parent.width - Theme.pad * 2
        y: 0
        onExpandedChanged: if (!expanded) list.positionViewAtBeginning()
    }
    property alias noteBar: quickAdd

    // Saves and collapses an open capture box. Called for Esc and for clicks elsewhere in
    // the overlay (the overlay's click-away layer, which leaves the box itself clickable).
    function collapseCapture() {
        if (!quickAdd.expanded)
            return false
        quickAdd.commit()
        return true
    }

    ListView {
        id: list
        anchors { left: parent.left; right: parent.right; top: quickAdd.bottom; topMargin: 8; bottom: parent.bottom; bottomMargin: 8 }
        anchors.leftMargin: 6
        anchors.rightMargin: 6
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: panel.shown

        delegate: Rectangle {
            id: row
            required property var modelData
            readonly property var bodyLines: panel.lines(modelData.body)
            width: ListView.view.width
            height: 48
            radius: Theme.radius
            color: rowHover.hovered ? Theme.hover : "transparent"
            Behavior on color { ColorAnimation { duration: Theme.fast } }

            Rectangle {
                id: dot
                anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                width: 8; height: 8; radius: 4
                color: row.modelData.tint === "none" ? Qt.rgba(1, 1, 1, 0.28)
                     : Qt.rgba(Theme.tint(row.modelData.tint).r, Theme.tint(row.modelData.tint).g, Theme.tint(row.modelData.tint).b, 1)
            }
            Icon {
                id: pin
                visible: row.modelData.pinned
                anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                glyph: Theme.icon.pinned; size: 11; color: Theme.textMuted
            }
            Column {
                anchors {
                    left: dot.right; leftMargin: 12
                    right: pin.visible ? pin.left : parent.right; rightMargin: 10
                    verticalCenter: parent.verticalCenter
                }
                spacing: 1
                Text {
                    width: parent.width
                    text: row.modelData.title !== "" ? row.modelData.title : (row.bodyLines[0] || "Empty note")
                    elide: Text.ElideRight
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody; font.weight: Font.Medium
                    color: Theme.text
                }
                Text {
                    width: parent.width
                    text: (row.modelData.title !== "" ? row.bodyLines : row.bodyLines.slice(1)).join("  ·  ")
                    visible: text !== ""
                    elide: Text.ElideRight
                    font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                    color: Theme.textMuted
                }
            }
            HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: panel.noteOpened(row.modelData, row) }
        }

        // thin scroll indicator
        Rectangle {
            visible: list.contentHeight > list.height
            x: list.width - 4
            y: list.visibleArea.yPosition * list.height
            width: 3; radius: 1.5
            height: list.visibleArea.heightRatio * list.height
            color: Qt.rgba(1, 1, 1, list.moving ? 0.35 : 0.15)
        }
    }

    // Empty / no-match state
    Column {
        visible: panel.shown.length === 0
        anchors { horizontalCenter: parent.horizontalCenter; top: quickAdd.bottom; topMargin: 28 }
        spacing: 12
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: panel.needle === "" ? "No notes yet" : "No matches"
            font.family: Theme.font; font.pixelSize: Theme.sizeBody
            color: Theme.textMuted
        }
        TextButton {
            visible: panel.needle !== ""
            primary: true
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.min(implicitWidth, panel.width - Theme.pad * 2)
            label: "Create note “" + panel.query.trim() + "”"
            onClicked: panel.createRequested(panel.query.trim())
        }
    }
}
