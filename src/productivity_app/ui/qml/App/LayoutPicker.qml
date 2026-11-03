import QtQuick

// Layout templates, in two forms:
//  - popup (bar: false): a glass card with the four thumbnails and "Reset to default". Fill the
//    overlay with it; openFor(item) places the card under the item that asked for it. A click
//    anywhere outside closes it (and goes no further).
//  - snap bar (bar: true): the same thumbnails in one row, slid in at the top of the screen while
//    a panel is dragged. Nothing is clicked there; the overlay hit-tests slots with hit() and
//    lights one up through hoverTemplate / hoverSlot.
Item {
    id: picker
    property bool bar: false
    property bool isOpen: false          // popup form
    property bool barShown: false        // snap-bar form
    readonly property bool open: bar ? barShown : isOpen
    property real shown: open ? 1 : 0
    Behavior on shown { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    visible: shown > 0.001

    readonly property var names: ["sketch", "focus", "rows", "columns"]
    readonly property var labels: ["Sketch", "One big focus", "Top & bottom rows", "Five columns"]
    property var fitMap: ({})            // { template: fits the screen }
    property string activeName: ""       // template whose shape the layout has now
    property int hoverTemplate: -1       // snap bar: template and slot under the dragged panel
    property int hoverSlot: -1
    readonly property int thumbW: bar ? 92 : 124
    readonly property int thumbH: bar ? 58 : 76
    property alias card: card
    signal templateChosen(string name)
    signal resetRequested()

    function openFor(item) {
        const p = item.mapToItem(picker, 0, 0)
        card.x = Math.max(8, Math.min(picker.width - card.width - 8, p.x + item.width - card.width))
        card.y = Math.min(picker.height - card.height - 8, p.y + item.height + 8)
        isOpen = true
    }
    function close() { isOpen = false }
    function cellAt(i) { return cells.itemAt(i) }     // used by the self-test

    // The template slot under a point in window coordinates: { tpl, name, slot, disabled }, or null.
    function hit(sx, sy) {
        for (let i = 0; i < cells.count; i++) {
            const cell = cells.itemAt(i)
            const p = cell.thumb.mapFromItem(null, sx, sy)
            if (p.x >= 0 && p.y >= 0 && p.x < cell.thumb.width && p.y < cell.thumb.height) {
                const slot = cell.thumb.slotAt(p.x, p.y)
                return slot < 0 ? null : { tpl: i, name: names[i], slot: slot, disabled: cell.disabled }
            }
        }
        return null
    }
    function containsScene(sx, sy) {
        const p = card.mapFromItem(null, sx, sy)
        return p.x >= 0 && p.y >= 0 && p.x < card.width && p.y < card.height
    }

    // Click-away layer (popup form)
    ClickBlocker {
        anchors.fill: parent
        enabled: !picker.bar
        visible: !picker.bar
        onDismissed: picker.close()
    }

    Rectangle {
        id: card
        width: grid.width + 24
        height: content.implicitHeight + 24
        radius: Theme.radius
        color: Qt.rgba(0.11, 0.115, 0.135, picker.bar ? 0.96 : 1)
        border.width: 1
        border.color: Theme.borderStrong
        opacity: picker.shown
        scale: 0.96 + 0.04 * picker.shown
        transformOrigin: picker.bar ? Item.Top : Item.TopRight

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // keep clicks inside the card

        Column {
            id: content
            x: 12; y: 12
            spacing: 8

            SectionLabel { visible: !picker.bar; text: "Layout"; leftPadding: 2 }
            Grid {
                id: grid
                columns: picker.bar ? 4 : 2
                spacing: 10
                Repeater {
                    id: cells
                    model: 4
                    Item {
                        id: cell
                        required property int index
                        readonly property string name: picker.names[index]
                        readonly property bool disabled: picker.fitMap[name] === false
                        readonly property bool active: picker.activeName === name
                        property alias thumb: miniature
                        width: picker.thumbW
                        height: picker.thumbH + (picker.bar ? 0 : 22)

                        Rectangle {
                            width: picker.thumbW; height: picker.thumbH
                            radius: Theme.radius
                            color: cellMouse.containsMouse && !cell.disabled ? Theme.hover : Qt.rgba(1, 1, 1, 0.04)
                            border.width: cell.active ? 1.5 : 1
                            border.color: cell.active ? Qt.rgba(1, 1, 1, 0.75) : Theme.border
                            Behavior on color { ColorAnimation { duration: Theme.fast } }
                        }
                        LayoutThumb {
                            id: miniature
                            x: 7; y: 7
                            width: picker.thumbW - 14; height: picker.thumbH - 14
                            template: cell.name
                            active: cell.active
                            disabled: cell.disabled
                            hovered: cellMouse.containsMouse
                            hoverSlot: picker.hoverTemplate === cell.index ? picker.hoverSlot : -1
                        }
                        Text {
                            visible: !picker.bar
                            y: picker.thumbH + 4
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: picker.labels[cell.index]
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                            color: cell.disabled ? Theme.textMuted : Theme.textSecondary
                        }
                        MouseArea {
                            id: cellMouse
                            anchors.fill: parent
                            acceptedButtons: Qt.AllButtons
                            hoverEnabled: true
                            cursorShape: cell.disabled ? Qt.ArrowCursor : Qt.PointingHandCursor
                            onClicked: (mouse) => {
                                if (!picker.bar && !cell.disabled && mouse.button === Qt.LeftButton)
                                    picker.templateChosen(cell.name)
                            }
                        }
                    }
                }
            }
            TextButton {
                visible: !picker.bar
                width: grid.width
                label: "Reset to default"
                onClicked: picker.resetRequested()
            }
        }
    }
}
