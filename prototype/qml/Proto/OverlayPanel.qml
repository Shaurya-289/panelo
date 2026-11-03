import QtQuick

// Glass card with a small uppercase title, an optional right-hand note and a
// "Double-click to expand" hint. Double-clicking the header emits expandRequested();
// pressing the header and moving past the drag threshold lifts the panel (the overlay
// moves it, see Overlay.qml) and emits dragStarted / dragMoved / dragEnded.
//
// The panel places itself: x/y/width/height follow `slot` (its rectangle in the layout) and
// glide there; while the overlay drags it, they follow `lift*` instead.
GlassPanel {
    id: panel
    property string title: ""
    property string meta: ""
    property bool expandable: true
    readonly property int headerHeight: 40
    signal expandRequested()
    signal dragStarted(real sx, real sy)     // scene coordinates of the press
    signal dragMoved(real sx, real sy)
    signal dragEnded(real sx, real sy)

    // ── Layout and drag state, driven by the overlay ────────────────
    property string panelKey: ""
    property var slot: null                  // { x, y, width, height } in the panel group
    property bool animateGeometry: true      // false while resizing, morphing or entering
    property bool glide: true                // false while the panel follows the cursor
    property bool following: false           // geometry comes from lift* (being dragged)
    property bool lifted: false              // the lifted look: smaller, solid, shadowed
    property real liftX: 0
    property real liftY: 0
    property real liftWidth: 0
    property real liftHeight: 0
    readonly property bool dragging: headerMouse.dragActive

    // Stops a drag in progress (Esc): the header ignores the cursor until it is released.
    function abortDrag() {
        headerMouse.aborted = true
        headerMouse.dragActive = false
    }

    x: following ? liftX : (slot ? slot.x : 0)
    y: following ? liftY : (slot ? slot.y : 0)
    width: following ? liftWidth : (slot ? slot.width : 0)
    height: following ? liftHeight : (slot ? slot.height : 0)
    Behavior on x { enabled: panel.glide && panel.animateGeometry; NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
    Behavior on y { enabled: panel.glide && panel.animateGeometry; NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
    Behavior on width { enabled: panel.glide && panel.animateGeometry; NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
    Behavior on height { enabled: panel.glide && panel.animateGeometry; NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }

    z: lifted ? 30 : 0
    scale: lifted ? 0.96 : 1
    opacity: 1
    // solid while lifted, so the panels underneath don't show through its text
    fill: lifted ? Theme.raised : Theme.panel
    Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    default property alias content: body.data

    readonly property bool hintFits: header.width - titleLabel.implicitWidth - metaLabel.width - Theme.pad * 3 - 20 > hintRow.implicitWidth

    // Soft shadow: faint rings just outside the card (the card itself is see-through, so the
    // shadow must not lie underneath it).
    Item {
        z: -1
        anchors.fill: parent
        opacity: panel.lifted ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        Repeater {
            model: 14
            Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -(index + 1)
                radius: Theme.radius + index + 1
                color: "transparent"
                border.width: 1
                border.color: Qt.rgba(0, 0, 0, 0.20 * Math.pow(1 - index / 14, 2.2))
            }
        }
    }

    // Anything pressed on a panel stays on the panel: a panel that is moving over another one
    // (while it glides) must not leak clicks to the panel below. Sits under the content.
    MouseArea { z: -1; anchors.fill: parent; acceptedButtons: Qt.AllButtons }

    Item {
        id: header
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: panel.headerHeight

        SectionLabel {
            id: titleLabel
            x: Theme.pad
            anchors.verticalCenter: parent.verticalCenter
            text: panel.title
        }
        Text {
            id: metaLabel
            anchors { right: parent.right; rightMargin: Theme.pad; verticalCenter: parent.verticalCenter }
            text: panel.meta
            width: text === "" ? 0 : implicitWidth
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: Theme.textMuted
        }
        Row {
            id: hintRow
            anchors { right: metaLabel.left; rightMargin: panel.meta === "" ? 0 : 12; verticalCenter: parent.verticalCenter }
            spacing: 5
            opacity: panel.expandable && panel.hintFits && headerMouse.containsMouse && !headerMouse.dragActive ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            Icon { anchors.verticalCenter: parent.verticalCenter; glyph: Theme.icon.expand; size: 11; color: Theme.textMuted }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Double-click to expand"
                font.family: Theme.font; font.pixelSize: 11
                color: Theme.textMuted
            }
        }

        // Drag handle and double-click target. A MouseArea (not a pointer handler), so the
        // press never reaches whatever lies under a panel that is being moved over another.
        MouseArea {
            id: headerMouse
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            hoverEnabled: true
            cursorShape: dragActive ? Qt.ClosedHandCursor : Qt.OpenHandCursor
            property bool dragActive: false
            property bool aborted: false
            property point pressScene

            onPressed: (mouse) => {
                dragActive = false
                aborted = false
                pressScene = mapToItem(null, mouse.x, mouse.y)
            }
            onPositionChanged: (mouse) => {
                if (!pressed || aborted || !(pressedButtons & Qt.LeftButton))
                    return
                const s = mapToItem(null, mouse.x, mouse.y)
                if (!dragActive) {
                    if (Math.hypot(s.x - pressScene.x, s.y - pressScene.y) < Qt.styleHints.startDragDistance)
                        return
                    dragActive = true
                    panel.dragStarted(pressScene.x, pressScene.y)
                    if (!dragActive)    // refused (the overlay is busy)
                        return
                }
                panel.dragMoved(s.x, s.y)
            }
            onReleased: (mouse) => {
                if (dragActive && !aborted) {
                    const s = mapToItem(null, mouse.x, mouse.y)
                    dragActive = false
                    panel.dragEnded(s.x, s.y)
                }
                dragActive = false
                aborted = false
            }
            onCanceled: {
                if (dragActive && !aborted)
                    panel.dragEnded(pressScene.x, pressScene.y)
                dragActive = false
                aborted = false
            }
            onDoubleClicked: (mouse) => {
                if (panel.expandable && mouse.button === Qt.LeftButton)
                    panel.expandRequested()
            }
        }
    }

    Item {
        id: body
        anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: parent.bottom }
    }
}
