import QtQuick

Text {
    property string glyph: ""
    property int size: 16
    text: glyph
    font.family: Theme.iconFont
    font.pixelSize: size
    color: Theme.textSecondary
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    renderType: Text.NativeRendering
}
