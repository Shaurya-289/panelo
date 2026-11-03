import QtQuick
import "smartparse.js" as Smart
import "dates.js" as Dates

// Smart-typing quick add. What the parser recognizes ("tomorrow 5pm", "!high") is highlighted in
// the text as rounded pills, and a preview row under the field says what Enter will create.
// submitted(parsed) carries the parser's result (title, due, hasTime, priority).
GlassPanel {
    id: bar
    property string placeholder: "Add a task…"
    property string glyph: Theme.icon.add
    property bool needsTime: false          // reminders: a time is required
    property alias text: box.text
    property alias input: box.input
    readonly property var parsed: Smart.parse(box.text)
    readonly property string preview: {
        const p = parsed
        const parts = []
        if (p.due !== null)
            parts.push(Dates.describe(p.due, p.hasTime))
        if (p.priority !== null)
            parts.push(Theme.priorityLabel(p.priority))
        if (parts.length === 0)
            return needsTime && box.text.trim() !== "" ? "No time yet: press Enter to pick one" : ""
        return parts.join("  ·  ")
    }
    readonly property bool previewShown: preview !== ""
    signal submitted(var parsed)

    function clear() { box.text = "" }
    function focusInput() { box.input.forceActiveFocus() }

    implicitHeight: 46 + (previewShown ? 26 : 0)
    height: implicitHeight
    hovered: box.input.activeFocus
    Behavior on height { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    clip: true

    MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: box.input.forceActiveFocus() }

    Icon {
        id: plus
        anchors { left: parent.left; leftMargin: 16; top: parent.top; topMargin: 15 }
        glyph: bar.glyph; size: 13; color: Theme.textMuted
    }

    // Pills sit under the text: they are drawn from the text positions of the recognized parts.
    Item {
        id: field
        anchors { left: plus.right; leftMargin: 14; right: parent.right; rightMargin: 16; top: parent.top }
        height: 46

        Repeater {
            model: bar.parsed.pills
            Rectangle {
                id: pill
                required property var modelData
                readonly property rect from: box.input.positionToRectangle(modelData.start)
                readonly property rect to: box.input.positionToRectangle(modelData.end)
                readonly property color tone: modelData.kind === "priority"
                    ? (modelData.value === "normal" ? Qt.rgba(1, 1, 1, 0.14) : Qt.rgba(Theme.priorityColor(modelData.value).r, Theme.priorityColor(modelData.value).g, Theme.priorityColor(modelData.value).b, 0.34))
                    : Qt.rgba(1, 1, 1, 0.17)
                x: box.x + from.x - 3
                y: box.y + from.y - 2
                width: Math.max(8, to.x - from.x + 6)
                height: from.height + 4
                radius: 5
                color: tone
                border.width: 1
                border.color: Qt.rgba(tone.r, tone.g, tone.b, Math.min(1, tone.a + 0.22))
            }
        }
        TextBox {
            id: box
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
            placeholder: bar.placeholder
            pixelSize: Theme.sizeTitle
            multiline: false
            Component.onCompleted: input.wrapMode = TextEdit.NoWrap
            onSubmitted: bar.submitted(bar.parsed)
        }
    }

    Text {
        visible: bar.previewShown
        anchors { left: parent.left; leftMargin: 16 + 13 + 14; top: parent.top; topMargin: 44 }
        text: "→  " + bar.preview
        font.family: Theme.font; font.pixelSize: Theme.sizeSmall
        color: Theme.textSecondary
    }
}
