import QtQuick
import QtQuick.Window
import "dates.js" as Dates
import "recurrence.js" as Recur

// The Full App's title-bar search: a slim box (an icon only, when the window is narrow) and a glass
// dropdown with the results grouped as NOTES, TASKS, REMINDERS and FILES (up to 5 each, plus
// "Show all in <section>" when there are more). Up/Down move, Enter opens, Esc clears or closes.
// The dropdown lives in the window's root layer (like the other popups): its click-away layer
// swallows the click that closes it, so nothing underneath reacts, and its mask leaves the box
// itself clickable. Everything clickable is a MouseArea.
//   noteChosen(note), taskChosen(task), reminderChosen(reminder), fileChosen(entry), showAllChosen(section)
Item {
    id: search
    property bool collapsed: false          // narrow window: only an icon until it is clicked
    property bool expanded: false           // ...and then the box
    property alias text: box.text
    property alias input: box.input
    property bool open: false               // false after a dismissal, until the text changes again
    property int current: 0                 // the highlighted entry, counted over the openable rows
    readonly property bool boxShown: !collapsed || expanded
    readonly property string needle: box.text.trim().toLowerCase()
    readonly property bool dropdownOpen: open && needle !== "" && boxShown
    readonly property bool focused: box.input.activeFocus
    readonly property int maxPerGroup: 5
    signal noteChosen(var note)
    signal taskChosen(var task)
    signal reminderChosen(var reminder)
    signal fileChosen(var entry)
    signal showAllChosen(string section)

    implicitHeight: 32
    height: 32

    // ── Results ─────────────────────────────────────────────────────
    function titleOf(note) { return note.title !== "" ? note.title : (note.body.split("\n")[0] || "Empty note") }
    function snippetOf(note, q) {
        const lines = note.body.split("\n").map(l => l.trim().replace(/^[-•]\s*/, "")).filter(l => l !== "")
        const hit = lines.find(l => l.toLowerCase().includes(q))
        if (note.title === "")
            return (hit && hit !== lines[0] ? hit : lines.slice(1).join("  ·  "))
        return hit || lines.join("  ·  ")
    }
    function byDue(a, b) {
        if (a.done !== b.done) return a.done ? 1 : -1
        return (a.due === null ? Infinity : a.due) - (b.due === null ? Infinity : b.due)
    }
    function compute(q) {
        if (q === "")
            return []
        const out = []
        const notes = Store.notes.filter(n => !n.deletedAt && !n.archived && (n.title + "\n" + n.body).toLowerCase().includes(q))
        const ordered = notes.filter(n => n.pinned).concat(notes.filter(n => !n.pinned))
        out.push({ key: "notes", label: "Notes", glyph: Theme.icon.note, total: ordered.length,
                   entries: ordered.slice(0, maxPerGroup).map(n => ({ title: titleOf(n), detail: snippetOf(n, q), ref: n })) })
        const tasks = Store.tasks.filter(t => !t.deletedAt && t.title.toLowerCase().includes(q)).sort(byDue)
        out.push({ key: "tasks", label: "Tasks", glyph: Theme.icon.tasks, total: tasks.length,
                   entries: tasks.slice(0, maxPerGroup).map(t => ({ title: t.title, ref: t,
                       detail: t.done ? "Done" : (t.due === null ? "No date" : Dates.formatDue(t.due, t.hasTime, Store.minute)) })) })
        const reminders = Store.reminders.filter(r => r.title.toLowerCase().includes(q)).sort(byDue)
        out.push({ key: "reminders", label: "Reminders", glyph: Theme.icon.reminder, total: reminders.length,
                   entries: reminders.slice(0, maxPerGroup).map(r => ({ title: r.title, ref: r,
                       detail: r.done ? "Done" : Recur.subtitle(r.repeat, r.anchor || r.due, Dates.formatWhen(r.due, Store.minute)) })) })
        const files = Library.search(q)
        out.push({ key: "files", label: "Files", glyph: Theme.icon.openFile, total: files.length,
                   entries: files.slice(0, maxPerGroup).map(f => ({ title: f.name, ref: f, glyph: f.isDir ? Theme.icon.folder : Theme.icon.openFile,
                       detail: f.parent && f.parent !== "" ? "Files / " + f.parent.split("/").join(" / ") : "Files" })) })
        return out.filter(g => g.total > 0)
    }
    readonly property var groups: compute(needle)
    // The flat list the dropdown shows: headers, results and "Show all" rows (navIndex counts the openable ones)
    readonly property var rows: {
        const out = []
        let nav = 0
        for (const g of groups) {
            out.push({ type: "header", label: g.label, count: g.total })
            for (const e of g.entries)
                out.push({ type: "result", group: g.key, glyph: e.glyph || g.glyph, title: e.title, detail: e.detail, ref: e.ref, navIndex: nav++ })
            if (g.total > g.entries.length)
                out.push({ type: "more", group: g.key, label: "Show all " + g.total + " in " + g.label, navIndex: nav++ })
        }
        if (out.length === 0 && needle !== "")
            out.push({ type: "empty" })
        return out
    }
    readonly property int navCount: rows.filter(r => r.navIndex !== undefined).length
    readonly property var counts: {
        const out = { notes: 0, tasks: 0, reminders: 0, files: 0 }
        for (const g of groups)
            out[g.key] = g.total
        return out
    }
    readonly property var shownCounts: {
        const out = { notes: 0, tasks: 0, reminders: 0, files: 0 }
        for (const g of groups)
            out[g.key] = g.entries.length
        return out
    }
    onRowsChanged: current = 0

    function activate(row) {
        if (!row)
            return
        dismiss()
        box.text = ""
        search.forceActiveFocus()
        if (row.type === "more") {
            search.showAllChosen(row.group)
        } else if (row.group === "notes") {
            search.noteChosen(row.ref)
        } else if (row.group === "tasks") {
            search.taskChosen(row.ref)
        } else if (row.group === "reminders") {
            search.reminderChosen(row.ref)
        } else if (row.group === "files") {
            search.fileChosen(row.ref)
        }
    }
    function activateCurrent() { activate(rows.find(r => r.navIndex === current)) }
    function move(delta) {
        if (navCount === 0)
            return
        current = (current + delta + navCount) % navCount
        const index = rows.findIndex(r => r.navIndex === current)
        if (index >= 0)
            list.positionViewAtIndex(index, ListView.Contain)
    }
    function dismiss() { open = false }
    onDropdownOpenChanged: { if (dropdownOpen) layer.place(); else layer.hold() }
    onNeedleChanged: open = needle !== ""

    // Focus the box (Ctrl+F): from the icon it opens up first
    function focusBox() {
        expanded = true
        box.input.forceActiveFocus()
        box.input.selectAll()
        if (needle !== "")
            open = true
    }
    // Esc: clears the text, else closes the dropdown, else lets go of the box. True when it used the key.
    function handleEscape() {
        if (box.text !== "") {
            box.text = ""
            return true
        }
        if (dropdownOpen) {
            dismiss()
            return true
        }
        if (focused || (collapsed && expanded)) {
            expanded = false
            search.forceActiveFocus()
            return true
        }
        return false
    }
    onFocusedChanged: {
        if (focused && needle !== "")
            open = true
        if (!focused && collapsed && box.text === "")
            expanded = false
    }

    // ── The box ─────────────────────────────────────────────────────
    Rectangle {
        id: field
        visible: search.boxShown
        anchors.fill: parent
        radius: Theme.radius
        color: search.focused ? Qt.rgba(1, 1, 1, 0.09) : Qt.rgba(1, 1, 1, 0.06)
        border.width: 1
        border.color: search.focused ? Theme.borderStrong : Theme.border
        Behavior on color { ColorAnimation { duration: Theme.fast } }

        // takes the clicks on the padding and the icon (the text field takes its own)
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            cursorShape: Qt.IBeamCursor
            onPressed: (mouse) => {
                mouse.accepted = true
                box.input.forceActiveFocus()
                if (search.needle !== "")
                    search.open = true
            }
            onWheel: (wheel) => { wheel.accepted = true }
        }
        Icon {
            id: glyph
            anchors { left: parent.left; leftMargin: 11; verticalCenter: parent.verticalCenter }
            glyph: Theme.icon.search; size: 12; color: Theme.textSecondary
        }
        TextBox {
            id: box
            anchors { left: glyph.right; leftMargin: 10; right: clear.left; rightMargin: 4; verticalCenter: parent.verticalCenter }
            placeholder: "Search…"
            pixelSize: Theme.sizeBody + 1
            multiline: false
            onSubmitted: search.activateCurrent()
            Component.onCompleted: input.Keys.forwardTo = [keys]
        }
        // A click in the text field (which handles its own mouse) reopens a dismissed dropdown; the press
        // is passed on to the text field untouched.
        MouseArea {
            anchors.fill: box
            acceptedButtons: Qt.LeftButton
            onPressed: (mouse) => {
                mouse.accepted = false
                if (search.needle !== "")
                    search.open = true
            }
        }
        Item {
            id: keys
            Keys.onUpPressed: (event) => { event.accepted = true; search.move(-1) }
            Keys.onDownPressed: (event) => { event.accepted = true; search.move(1) }
        }
        Text {
            visible: box.text === "" && !search.focused
            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
            text: "Ctrl+F"
            font.family: Theme.font; font.pixelSize: 11
            color: Theme.textMuted
        }
        IconButton {
            id: clear
            anchors { right: parent.right; rightMargin: 4; verticalCenter: parent.verticalCenter }
            width: 24; height: 24
            glyph: Theme.icon.close; iconSize: 9
            visible: box.text !== ""
            onClicked: { box.text = ""; box.input.forceActiveFocus() }
        }
    }
    // Narrow window: just the icon
    IconButton {
        id: iconOnly
        visible: !search.boxShown
        anchors.fill: parent
        glyph: Theme.icon.search
        iconSize: 14
        onClicked: search.focusBox()
    }

    // ── The dropdown ────────────────────────────────────────────────
    Item {
        id: layer
        parent: Window.contentItem
        anchors.fill: parent
        z: 900
        // The click-away layer outlives the card by a moment, like the other popups' fade-out: the
        // click that closes the dropdown (its press, then its release) must not reach the page.
        visible: search.dropdownOpen || holdTimer.running
        Timer { id: holdTimer; interval: 150 }

        ClickBlocker {
            anchors.fill: parent
            // the box itself stays clickable (typing, moving the caret)
            containmentMask: QtObject {
                function contains(point: point): bool {
                    const p = layer.mapToItem(search, point.x, point.y)
                    return !(p.x >= 0 && p.y >= 0 && p.x <= search.width && p.y <= search.height)
                }
            }
            onDismissed: search.dismiss()
        }

        function hold() { holdTimer.restart() }

        // centered under the box (placed when it opens, and again if the box moves or the window resizes)
        function place() {
            const p = search.mapToItem(layer, 0, 0)
            card.x = Math.max(8, Math.min(layer.width - card.width - 8, p.x + search.width / 2 - card.width / 2))
            card.y = p.y + search.height + 6
        }
        onWidthChanged: if (visible) place()
        Connections {
            target: search
            function onXChanged() { if (layer.visible) layer.place() }
            function onWidthChanged() { if (layer.visible) layer.place() }
        }

        Rectangle {
            id: card
            visible: search.dropdownOpen
            width: Math.min(460, layer.width - 16)
            height: Math.min(list.contentHeight + 12, Math.min(560, layer.height - y - 16))
            radius: Theme.radius
            color: Qt.rgba(0.11, 0.115, 0.135, 1)   // opaque, like the other popups
            border.width: 1
            border.color: Theme.borderStrong
            MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: (wheel) => { wheel.accepted = true } }

            ListView {
                id: list
                anchors { fill: parent; margins: 6 }
                clip: true
                interactive: contentHeight > height
                boundsBehavior: Flickable.StopAtBounds
                model: search.dropdownOpen ? search.rows : []
                delegate: Item {
                    id: row
                    required property var modelData
                    readonly property bool selected: modelData.navIndex !== undefined && modelData.navIndex === search.current
                    width: ListView.view.width
                    height: modelData.type === "header" ? 28 : modelData.type === "empty" ? 44 : 34

                    SectionLabel {
                        visible: row.modelData.type === "header"
                        anchors { left: parent.left; leftMargin: 10; bottom: parent.bottom; bottomMargin: 4 }
                        text: row.modelData.type === "header" ? row.modelData.label + "  ·  " + row.modelData.count : ""
                    }
                    Text {
                        visible: row.modelData.type === "empty"
                        anchors.centerIn: parent
                        text: "No matches"
                        font.family: Theme.font; font.pixelSize: Theme.sizeBody
                        color: Theme.textMuted
                    }
                    Rectangle {
                        visible: row.modelData.type === "result" || row.modelData.type === "more"
                        anchors.fill: parent
                        radius: 4
                        color: rowTap.pressed ? Theme.pressed : (row.selected || rowHover.hovered ? Theme.hover : "transparent")
                        Behavior on color { ColorAnimation { duration: Theme.fast } }

                        Icon {
                            id: rowGlyph
                            anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                            width: 16
                            glyph: row.modelData.type === "result" ? row.modelData.glyph : Theme.icon.chevronRight
                            size: row.modelData.type === "result" ? 13 : 10
                            color: Theme.textSecondary
                        }
                        Text {
                            id: rowTitle
                            anchors { left: rowGlyph.right; leftMargin: 10; verticalCenter: parent.verticalCenter }
                            width: row.modelData.type === "more" ? parent.width - 46
                                 : Math.min(implicitWidth, (parent.width - 46) * (rowDetail.text !== "" ? 0.6 : 1))
                            text: (row.modelData.type === "more" ? row.modelData.label : row.modelData.title) || ""
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody
                            font.weight: row.modelData.type === "more" ? Font.Medium : Font.Normal
                            color: row.modelData.type === "more" ? Theme.textSecondary : Theme.text
                        }
                        Text {
                            id: rowDetail
                            anchors { left: rowTitle.right; leftMargin: 10; right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                            text: row.modelData.type === "result" ? (row.modelData.detail || "") : ""
                            elide: Text.ElideRight
                            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                            color: Theme.textMuted
                        }
                        HoverHandler {
                            id: rowHover
                            cursorShape: Qt.PointingHandCursor
                            onHoveredChanged: if (hovered && row.modelData.navIndex !== undefined) search.current = row.modelData.navIndex
                        }
                        MouseArea {
                            id: rowTap
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton
                            onClicked: search.activate(row.modelData)
                        }
                    }
                }
            }
        }
    }

    // ── Used by the self-test ───────────────────────────────────────
    function rowItem(navIndex) {
        const index = rows.findIndex(r => r.navIndex === navIndex)
        return index < 0 ? null : list.itemAtIndex(index)
    }
    function centerOf(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    // The dropdown card in window coordinates, or null while it is closed
    function cardRect() {
        if (!dropdownOpen)
            return null
        const p = card.mapToItem(null, 0, 0)
        return { x: p.x, y: p.y, width: card.width, height: card.height }
    }
    function probe(name, index) {
        if (name === "box") return centerOf(field.visible ? field : iconOnly)
        if (name === "row") { const item = rowItem(index); return item ? centerOf(item) : null }
        if (name === "clear") return centerOf(clear)
        return null
    }
}
