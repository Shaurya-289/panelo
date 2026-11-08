import QtQuick

// One row of the file list: badge, name, then "kind · size" and "modified" on the right.
// In search mode a muted parent path sits under the name and is clickable.
Rectangle {
    id: row
    property var entry: ({})
    property bool showParent: false     // search result: show the parent folder path
    property bool wide: true            // false hides the right-hand details
    property bool renaming: false
    signal activated()
    signal contextRequested(real x, real y)
    signal parentActivated()
    signal renameAccepted(string text)

    readonly property var kindNames: ({
        "pdf": "PDF", "slides": "Slides", "doc": "Document", "sheet": "Spreadsheet", "image": "Image",
        "video": "Video", "audio": "Audio", "archive": "Archive", "code": "Code", "other": "File"
    })
    readonly property string kindSize: entry.isDir
        ? (entry.items === 1 ? "1 item" : entry.items + " items")
        : (kindNames[entry.kind] || "File") + (entry.size ? " · " + entry.size : "")
    readonly property string parentText: "Files" + (entry.parent ? " / " + entry.parent.split("/").join(" / ") : "")
    readonly property bool overParent: showParent && rowHover.hovered
        && rowHover.point.position.y > height / 2 && rowHover.point.position.x > nameColumn.x
        && rowHover.point.position.x < nameColumn.x + parentLabel.implicitWidth

    function beginRename() { renameField.begin(entry.name, entry.isDir) }
    onRenamingChanged: if (renaming) beginRename()

    height: showParent ? 46 : 40
    radius: Theme.radius
    color: rowHover.hovered ? Theme.hover : "transparent"
    Behavior on color { ColorAnimation { duration: Theme.fast } }

    FileBadge {
        id: badge
        anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
        size: 28
        kind: row.entry.kind || "other"
        ext: row.entry.ext || ""
        isDir: !!row.entry.isDir
    }

    Text {
        id: modified
        visible: row.wide && !row.showParent
        anchors { right: parent.right; rightMargin: 14; verticalCenter: parent.verticalCenter }
        width: visible ? 96 : 0
        horizontalAlignment: Text.AlignRight
        text: row.entry.modified || ""
        elide: Text.ElideRight
        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
        color: Theme.textMuted
    }
    Text {
        id: details
        visible: row.wide
        anchors { right: modified.visible ? modified.left : parent.right; rightMargin: modified.visible ? 10 : 14; verticalCenter: parent.verticalCenter }
        width: visible ? 150 : 0
        horizontalAlignment: Text.AlignRight
        text: row.kindSize
        elide: Text.ElideRight
        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
        color: Theme.textMuted
    }

    Column {
        id: nameColumn
        x: badge.x + badge.width + 12
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(40, (details.visible ? details.x : row.width - 4) - 10 - x)
        spacing: 1
        visible: !row.renaming
        Text {
            width: parent.width
            text: row.entry.name || ""
            elide: Text.ElideRight
            font.family: Theme.font; font.pixelSize: Theme.sizeBody
            color: Theme.text
        }
        Text {
            id: parentLabel
            visible: row.showParent
            width: Math.min(parent.width, implicitWidth)
            text: row.parentText
            elide: Text.ElideMiddle
            font.family: Theme.font; font.pixelSize: 11
            color: row.overParent ? Theme.text : Theme.textMuted
            font.underline: row.overParent
        }
    }
    RenameField {
        id: renameField
        visible: row.renaming
        x: nameColumn.x - 6
        width: Math.max(120, nameColumn.width + 6)
        anchors.verticalCenter: parent.verticalCenter
        onAccepted: (text) => row.renameAccepted(text)
    }

    HoverHandler {
        id: rowHover
        cursorShape: row.overParent ? Qt.PointingHandCursor : Qt.ArrowCursor
    }
    TapHandler {
        enabled: !row.renaming
        onTapped: (point) => {
            if (row.overParent) row.parentActivated()
            else row.activated()
        }
    }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: (point) => row.contextRequested(point.position.x, point.position.y)
    }
}
