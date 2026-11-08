import QtQuick

// Keep-style masonry: each note goes to the currently shortest column,
// using an estimate of its rendered height.
Item {
    id: root
    property var items: []
    property int minColumnWidth: 220
    property int spacing: Theme.gap
    property var editingId: -1
    signal noteOpened(var note, Item source)

    readonly property int columns: Math.max(1, Math.floor((width + spacing) / (minColumnWidth + spacing)))
    readonly property real columnWidth: (width - (columns - 1) * spacing) / columns

    function estimate(note) {
        const perLine = Math.max(12, Math.floor((columnWidth - Theme.pad * 2) / 7))
        let lines = 0
        for (const line of note.body.split("\n"))
            lines += Math.max(1, Math.ceil(line.length / perLine))
        lines = Math.min(lines, 9)
        return Theme.pad * 2 + 30 + (note.title !== "" ? 26 : 0) + lines * 19
    }

    readonly property var buckets: {
        const result = []
        const heights = []
        for (let c = 0; c < columns; ++c) { result.push([]); heights.push(0) }
        for (const note of items) {
            let target = 0
            for (let c = 1; c < columns; ++c)
                if (heights[c] < heights[target] - 1) target = c
            result[target].push(note)
            heights[target] += estimate(note) + spacing
        }
        return result
    }

    implicitHeight: row.implicitHeight

    // Every NoteCard currently shown (used by the self-test to find where to click).
    function cards() {
        const out = []
        for (let c = 0; c < columns; ++c) {
            const column = columnRepeater.itemAt(c)
            if (!column)
                continue
            for (const child of column.children)
                if (child.note !== undefined)
                    out.push(child)
        }
        return out
    }

    Row {
        id: row
        spacing: root.spacing
        Repeater {
            id: columnRepeater
            model: root.columns
            Column {
                id: column
                required property int index
                width: root.columnWidth
                spacing: root.spacing
                Repeater {
                    model: root.buckets[column.index]
                    NoteCard {
                        required property var modelData
                        width: column.width
                        note: modelData
                        hiddenForEditor: modelData.id === root.editingId
                        onOpened: (source) => root.noteOpened(modelData, source)
                    }
                }
            }
        }
    }
}
