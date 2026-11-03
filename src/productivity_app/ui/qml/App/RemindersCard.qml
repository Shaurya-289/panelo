import QtQuick
import "dates.js" as Dates
import "recurrence.js" as Recur

GlassPanel {
    id: card
    implicitHeight: column.implicitHeight + Theme.pad * 2

    Column {
        id: column
        x: Theme.pad; y: Theme.pad
        width: parent.width - Theme.pad * 2
        spacing: 6

        SectionLabel { text: "Upcoming" }
        Repeater {
            model: Store.upcomingReminders.slice(0, 5)
            Rectangle {
                id: row
                required property var modelData
                width: parent.width; height: 40
                radius: Theme.radius
                color: hover.hovered ? Theme.hover : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                Icon {
                    id: bell
                    anchors { left: parent.left; leftMargin: 6; verticalCenter: parent.verticalCenter }
                    glyph: Theme.icon.reminder; size: 13
                }
                Column {
                    anchors { left: bell.right; leftMargin: 12; right: repeatGlyph.visible ? repeatGlyph.left : time.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
                    Text {
                        width: parent.width
                        text: row.modelData.title; elide: Text.ElideRight
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody
                        color: Theme.text
                    }
                    Text {
                        width: parent.width
                        text: Recur.subtitle(row.modelData.repeat, row.modelData.anchor || row.modelData.due,
                                             Dates.relative(row.modelData.due, Store.minute))
                        elide: Text.ElideRight
                        font.family: Theme.font; font.pixelSize: 11
                        color: Theme.textMuted
                    }
                }
                Icon {
                    id: repeatGlyph
                    visible: row.modelData.repeat !== null
                    anchors { right: time.left; rightMargin: 5; verticalCenter: parent.verticalCenter }
                    glyph: Theme.icon.repeat; size: 11; color: Theme.textSecondary
                }
                Text {
                    id: time
                    anchors { right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
                    text: Dates.formatWhen(row.modelData.due, Store.minute)
                    font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                    color: Theme.textSecondary
                }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
            }
        }
    }
}
