import QtQuick
import QtQuick.Effects

// One tile of the file grid: a big badge (or a cropped thumbnail for images) and a
// two-line name.
Rectangle {
    id: tile
    property var entry: ({})
    property bool renaming: false
    signal activated()
    signal contextRequested(real x, real y)
    signal renameAccepted(string text)

    readonly property bool isImage: !tile.entry.isDir && entry.kind === "image" && entry.ext !== "svg"

    function beginRename() { renameField.begin(entry.name, entry.isDir) }
    onRenamingChanged: if (renaming) beginRename()

    width: 120
    height: 128
    radius: Theme.radius
    color: tileHover.hovered ? Theme.hover : "transparent"
    Behavior on color { ColorAnimation { duration: Theme.fast } }

    Item {
        id: preview
        y: 12
        anchors.horizontalCenter: parent.horizontalCenter
        width: tile.isImage ? 96 : 64
        height: 64

        FileBadge {
            visible: !tile.isImage
            anchors.centerIn: parent
            size: 64
            kind: tile.entry.kind || "other"
            ext: tile.entry.ext || ""
            isDir: !!tile.entry.isDir
        }
        Image {
            id: thumb
            anchors.fill: parent
            visible: false
            source: tile.isImage ? tile.entry.url : ""
            asynchronous: true
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: 256
            sourceSize.height: 256
        }
        MultiEffect {
            visible: tile.isImage && thumb.status === Image.Ready
            anchors.fill: parent
            source: thumb
            maskEnabled: true
            maskSource: thumbMask
        }
        Item {
            id: thumbMask
            anchors.fill: parent
            visible: false
            layer.enabled: true
            Rectangle { anchors.fill: parent; radius: 6 }
        }
        Rectangle {          // faint outline so a pale picture still reads as a tile
            visible: tile.isImage
            anchors.fill: parent
            radius: 6
            color: "transparent"
            border.width: 1
            border.color: Theme.border
        }
    }

    Text {
        visible: !tile.renaming
        x: 6; y: 84
        width: parent.width - 12
        height: 36
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignTop
        text: tile.entry.name || ""
        wrapMode: Text.Wrap
        maximumLineCount: 2
        elide: Text.ElideRight
        font.family: Theme.font; font.pixelSize: Theme.sizeBody
        color: Theme.text
    }
    RenameField {
        id: renameField
        visible: tile.renaming
        x: 4; y: 90
        width: parent.width - 8
        onAccepted: (text) => tile.renameAccepted(text)
    }

    HoverHandler { id: tileHover; cursorShape: Qt.PointingHandCursor }
    TapHandler {
        enabled: !tile.renaming
        onTapped: tile.activated()
    }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: (point) => tile.contextRequested(point.position.x, point.position.y)
    }
}
