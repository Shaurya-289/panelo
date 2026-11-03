import QtQuick
import "dates.js" as Dates

// The Tasks view: a smart quick-add field, then the tasks in groups (Overdue, Today, Tomorrow,
// This week, Later, No date). Press and drag a row to reschedule it: while dragging, every group
// (empty ones too) is a drop target. Click a row to open it in the task pop-out.
Item {
    id: page
    signal taskOpened(var task, Item source)

    readonly property var groups: Store.taskGroups
    property var dragId: -1                    // the id of the task being dragged, or -1
    property string hoverGroup: ""             // the group under the cursor while dragging
    readonly property bool dragging: dragId !== -1
    readonly property var dragTask: dragging ? Store.task(dragId) : null
    property real dragX: 0                     // the dragged row's top-left, in page coordinates
    property real dragY: 0
    property real dragW: 200
    property point grab: Qt.point(0, 0)
    property bool lastDropOk: false            // the last drop moved a task (self-test)

    readonly property int rowH: 38
    readonly property int headH: 32
    readonly property int groupGap: 12
    readonly property int emptyH: 44
    readonly property real listW: Math.min(page.width - 48, 880)

    // Every task shown, in a stable order (oldest first), so a row keeps its delegate (and glides) when
    // it moves to another group.
    readonly property var allShown: {
        let all = []
        for (const key of Dates.GROUPS)
            all = all.concat(groups[key])
        return all.sort((a, b) => a.createdAt !== b.createdAt ? a.createdAt - b.createdAt : (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    }

    // Where everything goes: heads[group] = { y, h, rowsY, rowsH }, rows[taskId] = y
    readonly property var layout: {
        const heads = {}, rows = {}
        let y = 0
        for (const key of Dates.GROUPS) {
            const list = groups[key]
            if (list.length === 0 && !dragging)
                continue
            const rowsH = list.length === 0 ? emptyH : list.length * rowH
            heads[key] = { y: y, h: headH, rowsY: y + headH, rowsH: rowsH }
            list.forEach((t, i) => { rows[t.id] = y + headH + i * rowH })
            y += headH + rowsH + groupGap
        }
        return { heads: heads, rows: rows, total: y }
    }
    function openCount(key) { return groups[key].filter(t => !t.done).length }

    // ── Drag and drop ───────────────────────────────────────────────
    function beginDrag(id, row, px, py) {
        dragId = id
        dragW = row.width
        grab = Qt.point(px, py)
        const p = row.mapToItem(page, 0, 0)
        dragX = p.x; dragY = p.y
        updateDrag(p.x + px, p.y + py)
    }
    function updateDrag(sx, sy) {   // page coordinates of the cursor
        if (!dragging)
            return
        dragX = sx - grab.x
        dragY = sy - grab.y
        const y = flick.contentItem.mapFromItem(page, sx, sy).y
        let found = ""
        for (const key of Dates.GROUPS) {
            const head = layout.heads[key]
            if (head && y >= head.y && y < head.rowsY + head.rowsH + groupGap / 2) {
                found = key
                break
            }
        }
        hoverGroup = found
    }
    function endDrag() {
        if (!dragging)
            return
        const id = dragId
        const target = hoverGroup
        const current = Dates.groupOf(Store.task(id).due, Store.now)
        lastDropOk = false
        if (target !== "" && target !== current && target !== "overdue")
            lastDropOk = Store.moveTaskToGroup(id, target)
        cancelDrag()
    }
    function cancelDrag() {
        dragId = -1
        hoverGroup = ""
    }
    // Scroll while a row is held near the top or bottom edge.
    Timer {
        interval: 30
        repeat: true
        running: page.dragging
        onTriggered: {
            const y = page.dragY + page.grab.y
            const speed = y < 70 ? -12 : y > page.height - 70 ? 12 : 0
            if (speed !== 0)
                flick.contentY = Math.max(0, Math.min(Math.max(0, flick.contentHeight - flick.height), flick.contentY + speed))
        }
    }

    // ── Used by the self-test: window coordinates of things ──────────
    function rowItem(id) {
        const i = allShown.findIndex(t => t.id === id)
        return i < 0 ? null : rowRepeater.itemAt(i)
    }
    function rowCenter(id) {
        const item = rowItem(id)
        if (!item)
            return null
        const p = item.mapToItem(null, item.width / 2 + 60, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function headerCenter(key) {
        const head = layout.heads[key]
        if (!head)
            return null
        const p = flick.contentItem.mapToItem(null, 120, head.y + head.h / 2)
        return { x: p.x, y: p.y }
    }
    function checkCenter(id) {
        const item = rowItem(id)
        if (!item)
            return null
        const p = item.mapToItem(null, 22, item.height / 2)
        return { x: p.x, y: p.y }
    }
    property alias addBar: add
    function scrollTop() { flick.contentY = 0 }
    // A drag without a mouse (screenshots): lift a row and hold it over a group's header.
    function demoDrag(id, group) {
        const row = rowItem(id)
        beginDrag(id, row, 90, row.height / 2)
        const head = layout.heads[group]
        const p = page.mapFromItem(flick.contentItem, (flick.width - listW) / 2 + 90, head.y + head.h / 2 + 4)
        updateDrag(p.x, p.y)
    }

    // ── Quick add ───────────────────────────────────────────────────
    SmartAdd {
        id: add
        x: (page.width - page.listW) / 2
        y: 8
        width: page.listW
        placeholder: "Add a task…  try “Call mom tomorrow 5pm !high”"
        onSubmitted: (p) => {
            Store.addTask(p.title, { due: p.due, hasTime: p.hasTime, priority: p.priority || "normal" })
            clear()
        }
    }

    // ── The list ────────────────────────────────────────────────────
    Flickable {
        id: flick
        anchors { left: parent.left; right: parent.right; top: add.bottom; topMargin: 14; bottom: parent.bottom }
        contentWidth: width
        contentHeight: page.layout.total + 24
        clip: true
        interactive: !page.dragging
        boundsBehavior: Flickable.StopAtBounds

        // the group under the dragged row: a soft outline (red where a drop is not allowed)
        Rectangle {
            readonly property var head: page.hoverGroup !== "" ? page.layout.heads[page.hoverGroup] : undefined
            readonly property bool allowed: page.hoverGroup !== "overdue"
            visible: page.dragging && head !== undefined
            x: (flick.width - page.listW) / 2 - 8
            y: head ? head.y - 4 : 0
            width: page.listW + 16
            height: head ? head.h + head.rowsH + 6 : 0
            radius: Theme.radius + 2
            color: allowed ? Qt.rgba(1, 1, 1, 0.07) : Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.10)
            border.width: 1.5
            border.color: allowed ? Qt.rgba(1, 1, 1, 0.55) : Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.6)
            Behavior on y { enabled: page.dragging; NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
            Behavior on height { enabled: page.dragging; NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
        }

        // group headers
        Repeater {
            model: Dates.GROUPS
            Item {
                id: header
                required property string modelData
                readonly property var head: page.layout.heads[modelData]
                visible: head !== undefined
                x: (flick.width - page.listW) / 2
                width: page.listW
                height: page.headH
                y: head ? head.y : 0
                Behavior on y { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
                SectionLabel {
                    anchors { left: parent.left; leftMargin: 4; verticalCenter: parent.verticalCenter; verticalCenterOffset: 4 }
                    text: Dates.GROUP_LABELS[header.modelData] + "  ·  " + page.openCount(header.modelData)
                    color: header.modelData === "overdue" ? Qt.rgba(0.93, 0.42, 0.44, 0.9) : Theme.textMuted
                }
                Text {
                    anchors { right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter; verticalCenterOffset: 4 }
                    visible: page.dragging && page.hoverGroup === header.modelData && header.modelData === "overdue"
                    text: "Can't drop here"
                    font.family: Theme.font; font.pixelSize: 11
                    color: Qt.rgba(0.93, 0.42, 0.44, 0.9)
                }
            }
        }

        // an empty group while dragging
        Repeater {
            model: Dates.GROUPS
            Rectangle {
                id: slotBox
                required property string modelData
                readonly property var head: page.layout.heads[modelData]
                visible: page.dragging && head !== undefined && page.groups[modelData].length === 0
                x: (flick.width - page.listW) / 2
                width: page.listW
                y: head ? head.rowsY + 2 : 0
                height: page.emptyH - 8
                radius: Theme.radius
                color: "transparent"
                border.width: 1
                border.color: Qt.rgba(1, 1, 1, 0.14)
                Text {
                    anchors.centerIn: parent
                    text: slotBox.modelData === "overdue" ? "Nothing can be dropped on Overdue" : "Drop here"
                    font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                    color: Theme.textMuted
                }
            }
        }

        // the rows
        Repeater {
            id: rowRepeater
            model: page.allShown.length
            Rectangle {
                id: row
                required property int index
                readonly property var t: page.allShown[index] || null
                readonly property bool isDragged: t !== null && page.dragId === t.id
                readonly property string group: t ? Dates.groupOf(t.due, Store.minute) : "none"
                readonly property bool overdue: group === "overdue" && t !== null && !t.done
                readonly property string dueText: t === null ? ""
                    : (group === "today" || group === "tomorrow") ? (t.hasTime ? Dates.clock(t.due) : "")
                    : Dates.formatDue(t.due, t.hasTime, Store.minute)
                visible: t !== null && page.layout.rows[t.id] !== undefined
                x: (flick.width - page.listW) / 2
                width: page.listW
                height: page.rowH - 4
                y: t ? (page.layout.rows[t.id] || 0) : 0
                radius: Theme.radius
                color: mouse.containsMouse && !page.dragging ? Theme.hover : "transparent"
                opacity: isDragged ? 0.3 : 1
                Behavior on y { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                Behavior on opacity { NumberAnimation { duration: Theme.fast } }

                // priority bar
                Rectangle {
                    anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
                    width: 3; radius: 1.5
                    color: row.t ? Theme.priorityColor(row.t.priority) : "transparent"
                    opacity: row.t && row.t.done ? 0.4 : 1
                }

                MouseArea {
                    id: mouse
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    hoverEnabled: true
                    preventStealing: true
                    cursorShape: page.dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor
                    property point pressAt
                    property bool lifted: false
                    property bool wasDragged: false     // a drag is not a click
                    onPressed: (event) => {
                        pressAt = mapToItem(page, event.x, event.y)
                        lifted = false
                        wasDragged = false
                    }
                    onPositionChanged: (event) => {
                        if (!pressed)
                            return
                        const p = mapToItem(page, event.x, event.y)
                        if (!lifted) {
                            if (Math.hypot(p.x - pressAt.x, p.y - pressAt.y) < Qt.styleHints.startDragDistance)
                                return
                            lifted = true
                            wasDragged = true
                            page.beginDrag(row.t.id, row, event.x, event.y)
                        }
                        page.updateDrag(p.x, p.y)
                    }
                    onReleased: {
                        if (lifted) {
                            lifted = false
                            page.endDrag()
                        }
                    }
                    onCanceled: {
                        if (lifted) {
                            lifted = false
                            page.cancelDrag()
                        }
                    }
                    onClicked: if (row.t && !wasDragged) page.taskOpened(row.t, row)
                }

                Checkbox {
                    id: check
                    anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
                    checked: row.t ? row.t.done : false
                    onToggled: Store.toggleTask(row.t.id)
                }
                Text {
                    id: dueLabel
                    anchors { right: parent.right; rightMargin: 14; verticalCenter: parent.verticalCenter }
                    text: row.dueText
                    font.family: Theme.font; font.pixelSize: Theme.sizeSmall
                    color: row.overdue ? Qt.rgba(0.93, 0.42, 0.44, 0.9) : Theme.textMuted
                }
                Text {
                    anchors {
                        left: check.right; leftMargin: 14
                        right: dueLabel.left; rightMargin: 12
                        verticalCenter: parent.verticalCenter
                    }
                    text: row.t ? row.t.title : ""
                    elide: Text.ElideRight
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
                    font.strikeout: row.t ? row.t.done : false
                    color: row.t && row.t.done ? Theme.textMuted : Theme.text
                }
            }
        }

        // empty state
        Column {
            visible: page.allShown.length === 0
            x: (flick.width - width) / 2
            y: 60
            spacing: 12
            Icon {
                anchors.horizontalCenter: parent.horizontalCenter
                glyph: Theme.icon.tasks; size: 56; color: Qt.rgba(1, 1, 1, 0.12)
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Nothing to do"
                font.family: Theme.font; font.pixelSize: Theme.sizeTitle + 2
                color: Theme.textMuted
            }
        }
    }

    // The row that follows the cursor
    Rectangle {
        visible: page.dragging && page.dragTask !== null
        x: page.dragX; y: page.dragY
        width: page.dragW; height: page.rowH - 4
        z: 50
        radius: Theme.radius
        color: Theme.raised
        border.width: 1
        border.color: Theme.borderStrong
        Rectangle {
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
            width: 3; radius: 1.5
            color: page.dragTask ? Theme.priorityColor(page.dragTask.priority) : "transparent"
        }
        Text {
            anchors { left: parent.left; leftMargin: 44; right: parent.right; rightMargin: 14; verticalCenter: parent.verticalCenter }
            text: page.dragTask ? page.dragTask.title : ""
            elide: Text.ElideRight
            font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
            color: Theme.text
        }
    }

    // thin scroll indicator
    Rectangle {
        visible: flick.contentHeight > flick.height
        x: page.width - 8
        y: flick.y + flick.visibleArea.yPosition * flick.height
        width: 3; radius: 1.5
        height: flick.visibleArea.heightRatio * flick.height
        color: Qt.rgba(1, 1, 1, flick.moving ? 0.35 : 0.15)
    }
}
