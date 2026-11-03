import QtQuick
import "layout.js" as Layout

// A mini picture of a layout template: five rounded slots with a 3px gap. The slot under
// `hoverSlot` lights up (while a panel is dragged over the thumbnail), `active` marks the
// current layout's shape, and `disabled` greys it out (the template does not fit the screen).
Item {
    id: thumb
    property string template: "sketch"
    property bool active: false
    property bool disabled: false
    property bool hovered: false
    property int hoverSlot: -1
    readonly property var slots: Layout.templateSlotRects(template, Qt.rect(0, 0, width, height), 3)

    // The slot (0-based) under a point in this item's coordinates, or -1.
    function slotAt(px, py) {
        for (let i = 0; i < slots.length; i++) {
            const r = slots[i]
            if (px >= r.x && px < r.x + r.width && py >= r.y && py < r.y + r.height)
                return i
        }
        return -1
    }

    opacity: disabled ? 0.35 : 1

    Repeater {
        model: 5
        Rectangle {
            required property int index
            readonly property var r: thumb.slots[index]
            x: r.x; y: r.y; width: r.width; height: r.height
            radius: 2
            color: thumb.hoverSlot === index ? Qt.rgba(1, 1, 1, 0.62)
                 : thumb.active ? Qt.rgba(1, 1, 1, 0.34)
                 : Qt.rgba(1, 1, 1, thumb.hovered ? 0.30 : 0.20)
            Behavior on color { ColorAnimation { duration: Theme.fast } }
        }
    }
}
