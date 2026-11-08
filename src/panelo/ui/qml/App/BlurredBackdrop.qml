import QtQuick
import QtQuick.Effects

// Blurred, darkened picture: the glass background shared by the overlay (a screen
// snapshot) and the Full App (the desktop wallpaper). The picture sits at
// (sourceX, sourceY) with size sourceWidth x sourceHeight, relative to this item,
// and is clipped to the item, so a window can show "its" part of a screen-sized image.
Item {
    id: backdrop
    property string source: ""
    property real sourceX: 0
    property real sourceY: 0
    property real sourceWidth: width
    property real sourceHeight: height
    property real blurAmount: 1       // 0..1
    property real darkness: 0.45      // opacity of the black layer on top

    clip: true

    Image {
        id: picture
        x: backdrop.sourceX
        y: backdrop.sourceY
        width: backdrop.sourceWidth
        height: backdrop.sourceHeight
        source: backdrop.source
        fillMode: Image.Stretch
        cache: false
        smooth: true
        visible: false
    }
    MultiEffect {
        x: picture.x
        y: picture.y
        width: picture.width
        height: picture.height
        source: picture
        blurEnabled: true
        blur: backdrop.blurAmount
        blurMax: 64
        autoPaddingEnabled: false
    }
    Rectangle {
        anchors.fill: parent
        color: "black"
        opacity: backdrop.darkness
    }
}
