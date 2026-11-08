import QtQuick

// Rounded square for a file: the extension text on a muted kind color, or a folder glyph.
Rectangle {
    id: badge
    property string kind: "other"
    property string ext: ""
    property bool isDir: false
    property int size: 28

    width: size
    height: size
    radius: 6
    color: Theme.kindColor(isDir ? "folder" : kind)

    Icon {
        visible: badge.isDir
        anchors.centerIn: parent
        glyph: Theme.icon.folderFilled
        size: Math.round(badge.size * 0.52)
        color: Qt.rgba(1, 1, 1, 0.92)
        renderType: Text.QtRendering   // no LCD fringes on a colored tile
    }
    Text {
        visible: !badge.isDir
        anchors.centerIn: parent
        width: badge.width - 4
        horizontalAlignment: Text.AlignHCenter
        text: badge.ext === "" ? "FILE" : badge.ext.toUpperCase().slice(0, 4)
        font.family: Theme.font
        font.pixelSize: Math.max(8, Math.round(badge.size * 0.3))
        font.weight: Font.DemiBold
        fontSizeMode: Text.HorizontalFit
        minimumPixelSize: 6
        color: "white"
    }
}
