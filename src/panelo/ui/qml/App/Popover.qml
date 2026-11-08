import QtQuick
import QtQuick.Window

// A small glass popover over the whole window: openFor(item) places the card next to the item
// that asked for it. Like the other popups it re-parents itself to the window root, has a
// click-away layer (ClickBlocker, so the click that closes it reaches nothing underneath) and a
// card that swallows its own clicks. Put the contents inside it; they are laid out in a column.
Item {
    id: pop
    property bool isOpen: false
    property int cardWidth: 260
    property string title: ""
    property real shown: isOpen ? 1 : 0
    property alias card: card
    default property alias content: body.data
    signal opened()
    signal closed()

    parent: Window.contentItem
    anchors.fill: parent
    z: 900
    visible: shown > 0.001
    Behavior on shown { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }

    function openFor(item, alignRight) {
        const p = item.mapToItem(pop, 0, 0)
        const h = card.height
        card.x = Math.max(8, Math.min(pop.width - card.width - 8, alignRight === false ? p.x : p.x + item.width - card.width))
        const below = p.y + item.height + 8
        card.y = below + h <= pop.height - 8 ? below : Math.max(8, p.y - h - 8)
        isOpen = true
        opened()
    }
    function openCentered() {
        card.x = (pop.width - card.width) / 2
        card.y = Math.max(8, (pop.height - card.height) * 0.4)
        isOpen = true
        opened()
    }
    function close() {
        if (!isOpen)
            return
        isOpen = false
        closed()
    }
    function handleEscape() {
        if (!isOpen)
            return false
        close()
        return true
    }

    ClickBlocker { anchors.fill: parent; onDismissed: pop.close() }

    Rectangle {
        id: card
        width: pop.cardWidth
        height: body.implicitHeight + 28 + (pop.title !== "" ? 22 : 0)
        radius: Theme.radius
        color: Qt.rgba(0.11, 0.115, 0.135, 1)   // opaque, like the time picker
        border.width: 1
        border.color: Theme.borderStrong
        opacity: pop.shown
        scale: 0.96 + 0.04 * pop.shown

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // keep clicks inside the card

        SectionLabel {
            visible: pop.title !== ""
            x: 14; y: 12
            text: pop.title
        }
        Column {
            id: body
            x: 14
            y: 14 + (pop.title !== "" ? 22 : 0)
            width: parent.width - 28
            spacing: 10
        }
    }
}
