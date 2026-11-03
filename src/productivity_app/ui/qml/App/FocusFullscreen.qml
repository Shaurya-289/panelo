import QtQuick

// The fullscreen focus view ("zen"): the blurred wallpaper, huge light digits, the session label
// and the phase dots. The controls (reset, play, skip) and the exit button fade out together with
// the cursor after `calmDelay` ms without mouse movement, and come back on any movement. It lies
// over everything in the window and blocks every click, hover and wheel event from the page below.
// Esc and a double-click on the time leave it (the window handles Esc and calls exitRequested).
Item {
    id: zen

    property bool active: false
    property string backdropSource: ""
    property real sourceX: 0
    property real sourceY: 0
    property real sourceWidth: width
    property real sourceHeight: height
    property real blurAmount: 1
    property real dim: 0.45
    property int calmDelay: 3000                     // the self-test shortens it
    property bool calm: false
    readonly property bool controlsShown: !calm
    readonly property bool resetHintShown: resetButton.hintShown
    readonly property string timeString: fmt(Store.focusRemaining)
    readonly property real progress: Store.focusTotal > 0 ? 1 - Store.focusRemaining / Store.focusTotal : 0
    property real shown: active ? 1 : 0
    signal exitRequested()

    visible: shown > 0.001
    opacity: shown
    z: 1100
    Behavior on shown { NumberAnimation { duration: 250; easing.type: Theme.easing } }

    function fmt(s) {
        const m = Math.floor(s / 60), r = s % 60
        return (m < 10 ? "0" : "") + m + ":" + (r < 10 ? "0" : "") + r
    }
    // Any mouse activity: the controls come back and the countdown to calm mode restarts.
    function poke() {
        if (!active)
            return
        calm = false
        calmTimer.restart()
    }
    onActiveChanged: {
        calm = false
        if (active) calmTimer.restart()
        else calmTimer.stop()
    }
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    // used by the self-test
    function probe(name) {
        const item = ({ play: playButton, reset: resetButton, skip: skipButton, exit: exitButton, time: timeBox })[name]
        return item ? center(item) : null
    }

    Text { id: probeText; visible: false; font.family: Theme.font; font.pixelSize: Math.round(zen.height * 0.22); font.weight: Font.Light }
    TextMetrics { id: m0; font: probeText.font; text: "0" }
    TextMetrics { id: m4; font: probeText.font; text: "4" }
    TextMetrics { id: m8; font: probeText.font; text: "8" }
    TextMetrics { id: mColon; font: probeText.font; text: ":" }

    Timer {
        id: calmTimer
        interval: zen.calmDelay
        onTriggered: zen.calm = true
    }

    BlurredBackdrop {
        anchors.fill: parent
        source: zen.backdropSource
        sourceX: zen.sourceX; sourceY: zen.sourceY
        sourceWidth: zen.sourceWidth; sourceHeight: zen.sourceHeight
        blurAmount: zen.blurAmount
        darkness: Math.min(0.92, zen.dim + 0.12)
    }

    // Blocks the page below; moving the mouse wakes the controls up.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        cursorShape: zen.calm ? Qt.BlankCursor : Qt.ArrowCursor
        onPositionChanged: zen.poke()
        onPressed: (mouse) => { mouse.accepted = true; zen.poke() }
        onWheel: (wheel) => { wheel.accepted = true }
    }

    // Leaves fullscreen (top right). Fades out with the other controls.
    IconButton {
        id: exitButton
        anchors { right: parent.right; top: parent.top; rightMargin: 28; topMargin: 34 }
        width: 44; height: 44
        glyph: Theme.icon.backToWindow; iconSize: 18
        hint: "Exit fullscreen (Esc)"
        opacity: zen.calm ? 0 : 1
        enabled: !zen.calm
        Behavior on opacity { NumberAnimation { duration: 500; easing.type: Theme.easing } }
        onClicked: zen.exitRequested()
    }

    Column {
        id: stack
        anchors { horizontalCenter: parent.horizontalCenter; verticalCenter: parent.verticalCenter; verticalCenterOffset: -zen.height * 0.05 }
        spacing: Math.round(zen.height * 0.03)

        // the time: every character in a cell of fixed width, so the digits don't jiggle
        Item {
            id: timeBox
            anchors.horizontalCenter: parent.horizontalCenter
            width: timeRow.width; height: timeRow.height
            Row {
                id: timeRow
                readonly property real digitW: Math.max(m0.advanceWidth, m4.advanceWidth, m8.advanceWidth)
                Repeater {
                    model: zen.timeString.length
                    Item {
                        required property int index
                        readonly property string ch: zen.timeString.charAt(index)
                        width: ch === ":" ? mColon.advanceWidth + 4 : timeRow.digitW
                        height: glyph.implicitHeight
                        Text {
                            id: glyph
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: parent.ch
                            font: probeText.font
                            color: Theme.text
                        }
                    }
                }
            }
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                onDoubleClicked: zen.exitRequested()
            }
        }

        // thin progress line
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.max(240, timeBox.width); height: 3; radius: 1.5
            color: Qt.rgba(1, 1, 1, 0.14)
            Rectangle {
                width: parent.width * zen.progress
                height: parent.height; radius: 1.5
                color: Theme.accent
                Behavior on width { NumberAnimation { duration: 400 } }
            }
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: Store.focusLabel
            font.family: Theme.font; font.pixelSize: Math.max(16, Math.round(zen.height * 0.028))
            color: Theme.textSecondary
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 12
            Repeater {
                model: 8
                Rectangle {
                    required property int index
                    anchors.verticalCenter: parent.verticalCenter
                    width: index % 2 === 0 ? 11 : 6
                    height: width; radius: width / 2
                    color: index < Store.focusPhase ? Theme.accent
                         : index === Store.focusPhase ? Qt.rgba(1, 1, 1, 0.6) : Qt.rgba(1, 1, 1, 0.16)
                }
            }
        }

        Item { width: 1; height: Math.round(zen.height * 0.02) }

        // controls: reset, play / pause, skip
        Row {
            id: controls
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 14
            opacity: zen.calm ? 0 : 1
            enabled: !zen.calm
            Behavior on opacity { NumberAnimation { duration: 500; easing.type: Theme.easing } }

            IconButton {
                id: resetButton
                width: 52; height: 52
                glyph: Theme.icon.reset; iconSize: 20
                hint: "Reset session"
                onClicked: Store.focusReset()
            }
            Rectangle {
                id: playButton
                width: 68; height: 52; radius: Theme.radius
                color: playTap.pressed ? "#cfd0d4" : playHover.hovered ? "#e6e7ea" : Theme.accent
                Icon {
                    anchors.centerIn: parent
                    glyph: Store.focusRunning ? Theme.icon.pause : Theme.icon.play
                    size: 20; color: Theme.accentInk
                }
                HoverHandler { id: playHover; cursorShape: Qt.PointingHandCursor }
                MouseArea { id: playTap; anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: Store.focusRunning = !Store.focusRunning }
            }
            IconButton {
                id: skipButton
                width: 52; height: 52
                glyph: Theme.icon.skip; iconSize: 20
                hint: "Skip"
                onClicked: Store.focusSkip()
            }
        }
    }

    // Mouse movement anywhere (also over the controls) wakes them up. A pointer handler is passive,
    // so the controls below keep their own hover and clicks. Only a changed position counts: the
    // synthetic hover that follows a control being disabled must not wake them again.
    HoverHandler {
        id: moves
        property real lastX: -1
        property real lastY: -1
        onPointChanged: {
            const p = point.position
            if (p.x === lastX && p.y === lastY)
                return
            lastX = p.x
            lastY = p.y
            zen.poke()
        }
    }
}
