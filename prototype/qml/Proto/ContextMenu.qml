import QtQuick
import QtQuick.Window

// Glass popup menu (icon + label rows). It re-parents itself to the window's root so
// it can appear over everything, and openAt() keeps it inside the window.
//   model: [{ key, label, glyph, danger, disabled, indent, separator }]
// Esc is routed in by the window (handleEscape() returns true when it closed the menu), because
// the overlay's own Esc shortcut would otherwise win.
Item {
    id: menu
    property bool isOpen: false
    property var model: []
    property string title: ""
    property int cardWidth: 224
    property int maxCardHeight: 340
    property real shown: isOpen ? 1 : 0
    signal chosen(string key)
    // Optional: with hoverDelay > 0, hovering a row for that many ms emits rowHovered(key)
    // (the Settings sound picker previews the sound that way).
    property int hoverDelay: 0
    signal rowHovered(string key)

    Timer {
        id: hoverTimer
        property string key: ""
        interval: menu.hoverDelay
        onTriggered: if (menu.isOpen) menu.rowHovered(key)
    }

    parent: Window.contentItem
    anchors.fill: parent
    z: 1000
    visible: shown > 0.001
    Behavior on shown { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }

    function openAt(item, px, py) {
        const p = item.mapToItem(menu, px, py)
        card.x = Math.max(8, Math.min(menu.width - card.width - 8, p.x))
        card.y = Math.max(8, Math.min(menu.height - card.height - 8, p.y))
        list.positionViewAtBeginning()
        isOpen = true
    }
    function close() { isOpen = false }
    function itemAtIndex(i) { return list.itemAtIndex(i) }   // used by the self-test
    function handleEscape() {
        if (!isOpen)
            return false
        isOpen = false
        return true
    }

    // Click-away layer (any button). It also swallows the release, the wheel and the hover,
    // so the click that closes the menu never reaches the file underneath.
    ClickBlocker { anchors.fill: parent; onDismissed: menu.close() }

    Rectangle {
        id: card
        width: menu.cardWidth
        height: Math.min(menu.maxCardHeight, list.contentHeight + 12 + (menu.title !== "" ? 26 : 0))
        radius: Theme.radius
        color: Qt.rgba(0.11, 0.115, 0.135, 1)   // opaque, like the time picker
        border.width: 1
        border.color: Theme.borderStrong
        opacity: menu.shown
        scale: 0.96 + 0.04 * menu.shown
        transformOrigin: Item.TopLeft

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // keep clicks inside

        SectionLabel {
            visible: menu.title !== ""
            x: 14; y: 8
            text: menu.title
        }
        ListView {
            id: list
            anchors { fill: parent; margins: 6; topMargin: 6 + (menu.title !== "" ? 26 : 0) }
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            model: menu.model

            delegate: Item {
                id: item
                required property var modelData
                width: ListView.view.width
                height: modelData.separator ? 9 : 32

                Rectangle {
                    visible: item.modelData.separator === true
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 8; rightMargin: 8 }
                    height: 1
                    color: Theme.border
                }
                Rectangle {
                    visible: item.modelData.separator !== true
                    anchors.fill: parent
                    radius: 4
                    color: itemTap.pressed ? Theme.pressed : (itemHover.hovered && !item.modelData.disabled ? Theme.hover : "transparent")
                    opacity: item.modelData.disabled ? 0.4 : 1
                    Behavior on color { ColorAnimation { duration: Theme.fast } }

                    Icon {
                        id: glyph
                        x: 10 + (item.modelData.indent || 0) * 14
                        anchors.verticalCenter: parent.verticalCenter
                        width: 16
                        glyph: item.modelData.glyph || ""
                        size: 13
                        color: item.modelData.danger ? Theme.danger : Theme.textSecondary
                    }
                    Text {
                        anchors { left: glyph.right; leftMargin: 10; right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                        text: item.modelData.label || ""
                        elide: Text.ElideRight
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody
                        color: item.modelData.danger ? Theme.danger : Theme.text
                    }
                    HoverHandler {
                        id: itemHover
                        cursorShape: item.modelData.disabled ? Qt.ArrowCursor : Qt.PointingHandCursor
                        onHoveredChanged: {
                            if (menu.hoverDelay <= 0 || item.modelData.disabled)
                                return
                            if (hovered) {
                                hoverTimer.key = item.modelData.key
                                hoverTimer.restart()
                            } else if (hoverTimer.key === item.modelData.key) {
                                hoverTimer.stop()
                            }
                        }
                    }
                    MouseArea {
                        id: itemTap
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton
                        enabled: !item.modelData.disabled
                        onClicked: { menu.isOpen = false; menu.chosen(item.modelData.key) }
                    }
                }
            }
        }
    }
}
