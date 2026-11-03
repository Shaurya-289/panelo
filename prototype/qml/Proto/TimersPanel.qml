import QtQuick

// The big focus timer on top, then the other active timers (one row, or a
// carousel with arrows when there is more than one).
OverlayPanel {
    id: panel
    title: "Timers"

    property int index: 0
    readonly property int count: Store.timers.length
    onCountChanged: index = Math.min(index, Math.max(0, count - 1))
    function goTo(i) { index = Math.max(0, Math.min(count - 1, i)) }

    Flickable {
        id: scroller
        anchors.fill: parent
        contentHeight: column.implicitHeight + 14
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: column
            x: Theme.pad
            width: parent.width - Theme.pad * 2
            spacing: 12

            FocusTimer { width: parent.width; showHeader: false }

            Rectangle {
                visible: panel.count > 0
                width: parent.width; height: 1
                color: Theme.border
            }

            // Carousel (a single timer is just a one-slide carousel without arrows)
            Column {
                visible: panel.count > 0
                width: parent.width
                spacing: 4

                Item {
                    id: viewport
                    width: parent.width
                    height: 58
                    clip: true

                    Row {
                        x: -panel.index * viewport.width
                        Behavior on x { NumberAnimation { duration: 260; easing.type: Theme.easing } }
                        Repeater {
                            model: panel.count
                            Item {
                                id: slide
                                required property int index
                                readonly property var t: Store.timers[index] || ({ name: "", remaining: 0, total: 1, running: false })
                                width: viewport.width
                                height: viewport.height

                                Text {
                                    id: nameLabel
                                    anchors { left: parent.left; right: toggle.left; rightMargin: 8; top: parent.top }
                                    text: slide.t.name
                                    elide: Text.ElideRight
                                    font.family: Theme.font; font.pixelSize: Theme.sizeBody
                                    color: Theme.textSecondary
                                }
                                Text {
                                    anchors { left: parent.left; top: nameLabel.bottom; topMargin: 1 }
                                    text: panel.timerText(slide.t.remaining)
                                    font.family: Theme.font; font.pixelSize: 24; font.weight: Font.Light
                                    color: slide.t.running ? Theme.text : Theme.textSecondary
                                }
                                IconButton {
                                    id: toggle
                                    anchors { right: parent.right; verticalCenter: parent.verticalCenter; verticalCenterOffset: -3 }
                                    glyph: slide.t.running ? Theme.icon.pause : Theme.icon.play
                                    onClicked: Store.toggleTimer(slide.t.id)
                                }
                                Rectangle {
                                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                                    height: 2; radius: 1
                                    color: Qt.rgba(1, 1, 1, 0.12)
                                    Rectangle {
                                        width: parent.width * Math.max(0, Math.min(1, 1 - slide.t.remaining / slide.t.total))
                                        height: parent.height; radius: 1
                                        color: slide.t.running ? Theme.accent : Qt.rgba(1, 1, 1, 0.45)
                                        Behavior on width { NumberAnimation { duration: 400 } }
                                    }
                                }
                            }
                        }
                    }
                }

                Row {
                    visible: panel.count > 1
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 6
                    IconButton {
                        glyph: Theme.icon.chevronLeft; iconSize: 11
                        width: 26; height: 26
                        opacity: panel.index > 0 ? 1 : 0.35
                        onClicked: panel.goTo(panel.index - 1)
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 40
                        horizontalAlignment: Text.AlignHCenter
                        text: (panel.index + 1) + " / " + panel.count
                        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                        color: Theme.textMuted
                    }
                    IconButton {
                        glyph: Theme.icon.chevronRight; iconSize: 11
                        width: 26; height: 26
                        opacity: panel.index < panel.count - 1 ? 1 : 0.35
                        onClicked: panel.goTo(panel.index + 1)
                    }
                }
            }
        }
    }

    function timerText(s) {
        const m = Math.floor(s / 60), r = s % 60
        return (m < 10 ? "0" : "") + m + ":" + (r < 10 ? "0" : "") + r
    }
}
