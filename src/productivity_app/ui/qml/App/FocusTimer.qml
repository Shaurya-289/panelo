import QtQuick

// Pomodoro content: big time, thin progress line, 8-phase dots, controls.
// State lives in Store so the Full App rail and the overlay stay in sync.
Column {
    id: timer
    property bool showHeader: true
    readonly property bool compact: width < 200
    spacing: 10

    function fmt(s) {
        const m = Math.floor(s / 60), r = s % 60
        return (m < 10 ? "0" : "") + m + ":" + (r < 10 ? "0" : "") + r
    }
    readonly property string sessionText: Store.focusLabel

    Item {
        width: parent.width; height: 16
        visible: timer.showHeader
        SectionLabel { text: "Focus"; anchors.verticalCenter: parent.verticalCenter }
        Text {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            text: timer.sessionText
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: Theme.textMuted
        }
    }
    Item {
        width: parent.width
        height: bigTime.implicitHeight
        Text {
            id: bigTime
            text: timer.fmt(Store.focusRemaining)
            font.family: Theme.font
            font.pixelSize: 44
            font.weight: Font.Light
            color: Theme.text
        }
        Text {
            visible: !timer.showHeader
            anchors { right: parent.right; baseline: bigTime.baseline }
            text: timer.sessionText
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: Theme.textMuted
        }
    }
    Rectangle {
        width: parent.width; height: 2; radius: 1
        color: Qt.rgba(1, 1, 1, 0.12)
        Rectangle {
            width: parent.width * (1 - Store.focusRemaining / Store.focusTotal)
            height: parent.height; radius: 1
            color: Theme.accent
            Behavior on width { NumberAnimation { duration: 400 } }
        }
    }
    Item {
        width: parent.width; height: 30
        Row {
            visible: !timer.compact
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            Repeater {
                model: 8
                Rectangle {
                    required property int index
                    anchors.verticalCenter: parent.verticalCenter
                    width: index % 2 === 0 ? 8 : 4
                    height: width; radius: width / 2
                    color: index < Store.focusPhase ? Theme.accent
                         : index === Store.focusPhase ? Qt.rgba(1, 1, 1, 0.6) : Qt.rgba(1, 1, 1, 0.16)
                }
            }
        }
        Row {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            spacing: 4
            IconButton { id: resetButton; glyph: Theme.icon.reset; hint: "Reset session"; onClicked: Store.focusReset() }
            IconButton { glyph: Theme.icon.skip; onClicked: Store.focusSkip() }
            Rectangle {
                width: 30; height: 30; radius: Theme.radius
                color: playHover.hovered ? "#e6e7ea" : Theme.accent
                Icon {
                    anchors.centerIn: parent
                    glyph: Store.focusRunning ? Theme.icon.pause : Theme.icon.play
                    size: 13; color: Theme.accentInk
                }
                HoverHandler { id: playHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: Store.focusRunning = !Store.focusRunning }
            }
        }
    }
}
