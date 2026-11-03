import QtQuick
import QtQuick.Dialogs
import QtQuick.Shapes

// The mini file explorer shared by the overlay's Files panel and the Full App's Files
// view: breadcrumb, toolbar, list or grid, drag-and-drop, right-click menu, search.
// `rel` is owned by the host: the browser asks for navigation with navigated(rel).
Item {
    id: browser
    property string rel: ""
    property string mode: "list"          // "list" | "grid"
    property string query: ""             // non-empty: show search results instead
    property bool showModeToggle: false   // the Full App shows the list/grid toggle
    property int inset: Theme.pad
    readonly property bool compact: width < 500
    readonly property bool searching: query.trim() !== ""
    readonly property string folderName: rel === "" ? "Files" : rel.split("/").pop()

    readonly property bool menuOpen: menu.isOpen || movePicker.isOpen
    property var entries: []
    property string renamingRel: ""
    property string errorText: ""
    property bool dropActive: false       // also set directly by `--state files-drop`

    signal navigated(string rel)
    signal clearQueryRequested()          // a result was opened, leave the search
    signal modeRequested(string mode)
    signal launched()                     // a file was opened, or shown in Explorer (another app takes over)

    readonly property var crumbs: {
        const out = [{ label: "Files", rel: "" }]
        let acc = ""
        for (const part of (rel === "" ? [] : rel.split("/"))) {
            acc = acc === "" ? part : acc + "/" + part
            out.push({ label: part, rel: acc })
        }
        if (out.length <= 4)
            return out
        return [out[0], { label: "…", rel: out[out.length - 3].rel }, out[out.length - 2], out[out.length - 1]]
    }

    function refresh() {
        if (rel !== "" && !Library.isDir(rel)) {
            navigated("")
            return
        }
        const text = query.trim()   // not `searching`: its binding may not be updated yet
        entries = text !== "" ? Library.search(text) : Library.list(rel)
    }
    onRelChanged: refresh()
    onQueryChanged: refresh()
    Component.onCompleted: refresh()
    Connections {
        target: Library
        function onChanged() { browser.refresh() }
        function onAsyncError(error) { browser.report(error) }     // a big copy that failed
    }

    function report(message) {
        if (message === "")
            return
        errorText = message
        errorTimer.restart()
    }
    Timer { id: errorTimer; interval: 4000; onTriggered: browser.errorText = "" }

    function go(target) {
        if (searching)
            clearQueryRequested()
        navigated(target)
    }
    function activate(entry) {
        if (entry.isDir)
            go(entry.rel)
        else {
            Library.open(entry.rel)
            launched()
        }
    }
    function addUrls(urls) { report(Library.addFiles(urls.map(u => u.toString()), rel)) }
    function pasteHere() { report(Library.pasteInto(rel)) }
    function newFolder() {
        const name = Library.uniqueFolderName(rel)
        const error = Library.createFolder(rel, name)
        report(error)
        if (error === "")
            renamingRel = rel === "" ? name : rel + "/" + name
    }
    function commitRename(entryRel, text) {
        if (renamingRel !== entryRel)
            return
        renamingRel = ""
        report(Library.rename(entryRel, text))
    }
    // Used by `--screenshot` and the self-test
    function demoContext(index) {
        const item = listView.itemAtIndex(index)
        if (item)
            openMenu(entries[index], item, 220, 24)
    }
    function demoMove(index) {
        const item = listView.itemAtIndex(index)
        if (!item)
            return
        ctxEntry = entries[index]; ctxItem = item; ctxX = 220; ctxY = 24
        openMovePicker()
    }
    function demoRename(index) { renamingRel = entries[index].rel }
    // Centre of a control in window coordinates (the self-test clicks it with QTest).
    function probe(name, index) {
        let item = null
        if (name === "row") item = listView.itemAtIndex(index)
        else if (name === "tile") item = gridView.itemAtIndex(index)
        else if (name === "menuItem") item = menu.itemAtIndex(index)
        else if (name === "newFolder") item = newFolderButton
        else if (name === "pasteButton") item = pasteButton
        else if (name === "back") item = back
        else if (name === "listToggle") item = listToggle
        else if (name === "gridToggle") item = gridToggle
        if (!item)
            return null
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    // Esc order: move picker, menu, inline rename. Returns true when it used the key.
    function handleEscape() {
        if (movePicker.handleEscape() || menu.handleEscape())
            return true
        if (renamingRel !== "") {
            renamingRel = ""
            return true
        }
        return false
    }

    // ── Right-click menu ────────────────────────────────────────────
    property var ctxEntry: null
    property var ctxItem: null
    property real ctxX: 0
    property real ctxY: 0

    function openMenu(entry, item, x, y) {
        ctxEntry = entry; ctxItem = item; ctxX = x; ctxY = y
        menu.model = [
            { key: "rename", label: "Rename", glyph: Theme.icon.rename },
            { key: "delete", label: "Delete", glyph: Theme.icon.trash, danger: true },
            { key: "move", label: "Move to folder…", glyph: Theme.icon.moveTo },
            { key: "explorer", label: "Show in Explorer", glyph: Theme.icon.showInFolder },
            { key: "copy", label: "Copy", glyph: Theme.icon.copy }
        ]
        menu.openAt(item, x, y)
    }
    function openMovePicker() {
        const entry = ctxEntry
        const parentRel = entry.rel.includes("/") ? entry.rel.substring(0, entry.rel.lastIndexOf("/")) : ""
        const blocked = (target) => target === parentRel
            || (entry.isDir && (target === entry.rel || target.startsWith(entry.rel + "/")))
        const list = [{ key: "", label: "Files", glyph: Theme.icon.folder, indent: 0, disabled: blocked("") }]
        for (const folder of Library.folders())
            list.push({ key: folder, label: folder.split("/").pop(), glyph: Theme.icon.folder,
                        indent: folder.split("/").length, disabled: blocked(folder) })
        movePicker.model = list
        movePicker.openAt(ctxItem, ctxX, ctxY)
    }
    ContextMenu {
        id: menu
        onChosen: (key) => {
            const entry = browser.ctxEntry
            if (key === "rename") browser.renamingRel = entry.rel
            else if (key === "delete") browser.report(Library.remove(entry.rel))
            else if (key === "move") Qt.callLater(browser.openMovePicker)
            else if (key === "explorer") { Library.showInExplorer(entry.rel); browser.launched() }
            else if (key === "copy") browser.report(Library.copyToClipboard(entry.rel))
        }
    }
    ContextMenu {
        id: movePicker
        title: "MOVE TO"
        cardWidth: 250
        onChosen: (key) => browser.report(Library.move(browser.ctxEntry.rel, key))
    }

    FileDialog {
        id: picker
        title: "Add files"
        fileMode: FileDialog.OpenFiles
        onAccepted: browser.addUrls(picker.selectedFiles)
    }

    // ── Drag & drop (from Explorer or the desktop) ──────────────────
    DropArea {
        anchors.fill: parent
        onEntered: (drag) => {
            drag.accepted = drag.hasUrls
            if (drag.hasUrls)
                browser.dropActive = true
        }
        onExited: browser.dropActive = false
        onDropped: (drop) => {
            browser.dropActive = false
            if (drop.hasUrls) {
                drop.acceptProposedAction()
                browser.addUrls(drop.urls)
            }
        }
    }

    // ── Header: breadcrumb and toolbar ──────────────────────────────
    component ToolButton: Rectangle {
        id: tool
        property string glyph: ""
        property string label: ""
        property bool showLabel: true
        signal clicked()
        implicitWidth: showLabel ? toolRow.implicitWidth + 22 : 30
        implicitHeight: 30
        radius: Theme.radius
        color: toolTap.pressed ? Theme.pressed : toolHover.hovered ? Theme.hover : Qt.rgba(1, 1, 1, 0.05)
        border.width: 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Row {
            id: toolRow
            anchors.centerIn: parent
            spacing: 7
            Icon { anchors.verticalCenter: parent.verticalCenter; glyph: tool.glyph; size: 12; color: Theme.textSecondary }
            Text {
                visible: tool.showLabel
                anchors.verticalCenter: parent.verticalCenter
                text: tool.label
                font.family: Theme.font; font.pixelSize: Theme.sizeBody
                color: Theme.text
            }
        }
        HoverHandler { id: toolHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { id: toolTap; onTapped: tool.clicked() }
    }

    Item {
        id: bar
        anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: browser.inset - 6; rightMargin: browser.inset }
        height: 34

        Row {
            id: toolbar
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            spacing: 6
            ToolButton {
                id: newFolderButton
                visible: !browser.searching
                glyph: Theme.icon.newFolder; label: "New folder"; showLabel: !browser.compact
                onClicked: browser.newFolder()
            }
            ToolButton {
                visible: !browser.searching
                glyph: Theme.icon.add; label: "Add file"; showLabel: !browser.compact
                onClicked: picker.open()
            }
            IconButton {
                id: pasteButton
                visible: !browser.searching
                glyph: Theme.icon.paste; iconSize: 13
                onClicked: browser.pasteHere()
            }
            Rectangle {
                visible: browser.showModeToggle
                anchors.verticalCenter: parent.verticalCenter
                width: 1; height: 18; color: Theme.border
            }
            IconButton {
                id: listToggle
                visible: browser.showModeToggle
                glyph: Theme.icon.listView; iconSize: 13
                active: browser.mode === "list"
                onClicked: browser.modeRequested("list")
            }
            IconButton {
                id: gridToggle
                visible: browser.showModeToggle
                glyph: Theme.icon.gridView; iconSize: 13
                active: browser.mode === "grid"
                onClicked: browser.modeRequested("grid")
            }
        }

        Item {
            anchors { left: parent.left; right: toolbar.left; rightMargin: 8; top: parent.top; bottom: parent.bottom }
            clip: true

            // Search: a plain label instead of the breadcrumb
            Row {
                visible: browser.searching
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                x: 6
                Icon { anchors.verticalCenter: parent.verticalCenter; glyph: Theme.icon.search; size: 12; color: Theme.textMuted }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Results for “" + browser.query.trim() + "”"
                    font.family: Theme.font; font.pixelSize: Theme.sizeBody
                    color: Theme.textSecondary
                }
            }

            Row {
                visible: !browser.searching
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                IconButton {
                    id: back
                    anchors.verticalCenter: parent.verticalCenter
                    width: 28; height: 28
                    glyph: Theme.icon.chevronLeft; iconSize: 12
                    opacity: browser.rel !== "" ? 1 : 0.3
                    onClicked: {
                        if (browser.rel !== "")
                            browser.go(browser.rel.includes("/") ? browser.rel.substring(0, browser.rel.lastIndexOf("/")) : "")
                    }
                }
                Repeater {
                    model: browser.crumbs
                    Row {
                        id: crumb
                        required property var modelData
                        required property int index
                        readonly property bool last: index === browser.crumbs.length - 1
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: crumb.index > 0
                            text: "/"
                            font.family: Theme.font; font.pixelSize: Theme.sizeBody
                            color: Theme.textMuted
                        }
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: label.implicitWidth + 12; height: 26
                            radius: 4
                            color: crumbHover.hovered && !crumb.last ? Theme.hover : "transparent"
                            Text {
                                id: label
                                anchors.centerIn: parent
                                text: crumb.modelData.label
                                font.family: Theme.font; font.pixelSize: Theme.sizeBody
                                font.weight: crumb.last ? Font.Medium : Font.Normal
                                color: crumb.last ? Theme.text : (crumbHover.hovered ? Theme.text : Theme.textSecondary)
                            }
                            HoverHandler { id: crumbHover; cursorShape: crumb.last ? Qt.ArrowCursor : Qt.PointingHandCursor }
                            TapHandler { enabled: !crumb.last; onTapped: browser.go(crumb.modelData.rel) }
                        }
                    }
                }
            }
        }
    }

    // Errors from Library calls: a small muted red line for 4 s
    Item {
        id: errorLine
        anchors { left: parent.left; right: parent.right; top: bar.bottom; leftMargin: browser.inset; rightMargin: browser.inset }
        height: browser.errorText !== "" ? 20 : 0
        clip: true
        Behavior on height { NumberAnimation { duration: Theme.fast } }
        Text {
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
            text: browser.errorText
            elide: Text.ElideRight
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: Theme.danger
            opacity: 0.85
        }
    }

    // ── Content ─────────────────────────────────────────────────────
    readonly property bool asGrid: mode === "grid" && !searching

    ListView {
        id: listView
        visible: !browser.asGrid
        anchors { left: parent.left; right: parent.right; top: errorLine.bottom; bottom: parent.bottom; topMargin: 4 }
        anchors.leftMargin: 6
        anchors.rightMargin: 6
        anchors.bottomMargin: 8
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: browser.asGrid ? [] : browser.entries
        spacing: 1

        delegate: FileRow {
            id: fileRow
            required property var modelData
            width: ListView.view.width
            entry: modelData
            showParent: browser.searching
            wide: browser.width > 460
            renaming: browser.renamingRel === modelData.rel
            onActivated: browser.activate(modelData)
            onParentActivated: browser.go(modelData.parent)
            onContextRequested: (x, y) => browser.openMenu(modelData, fileRow, x, y)
            onRenameAccepted: (text) => browser.commitRename(modelData.rel, text)
        }

        Rectangle {
            visible: listView.contentHeight > listView.height
            x: listView.width - 4
            y: listView.visibleArea.yPosition * listView.height
            width: 3; radius: 1.5
            height: listView.visibleArea.heightRatio * listView.height
            color: Qt.rgba(1, 1, 1, listView.moving ? 0.35 : 0.15)
        }
    }

    GridView {
        id: gridView
        visible: browser.asGrid
        anchors { left: parent.left; right: parent.right; top: errorLine.bottom; bottom: parent.bottom; topMargin: 8 }
        anchors.leftMargin: 10
        anchors.rightMargin: 10
        anchors.bottomMargin: 8
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        readonly property int columns: Math.max(1, Math.floor(width / 132))
        cellWidth: width / columns
        cellHeight: 140
        model: browser.asGrid ? browser.entries : []

        delegate: Item {
            id: cell
            required property var modelData
            width: GridView.view.cellWidth
            height: GridView.view.cellHeight
            FileTile {
                id: fileTile
                anchors.horizontalCenter: parent.horizontalCenter
                y: 4
                entry: cell.modelData
                renaming: browser.renamingRel === cell.modelData.rel
                onActivated: browser.activate(cell.modelData)
                onContextRequested: (x, y) => browser.openMenu(cell.modelData, fileTile, x, y)
                onRenameAccepted: (text) => browser.commitRename(cell.modelData.rel, text)
            }
        }

        Rectangle {
            visible: gridView.contentHeight > gridView.height
            x: gridView.width - 4
            y: gridView.visibleArea.yPosition * gridView.height
            width: 3; radius: 1.5
            height: gridView.visibleArea.heightRatio * gridView.height
            color: Qt.rgba(1, 1, 1, gridView.moving ? 0.35 : 0.15)
        }
    }

    // Empty state
    Column {
        visible: browser.entries.length === 0
        anchors { horizontalCenter: parent.horizontalCenter; verticalCenter: parent.verticalCenter; verticalCenterOffset: 16 }
        spacing: 12
        Icon {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: !browser.searching && browser.height > 200
            glyph: Theme.icon.folder; size: 44; color: Qt.rgba(1, 1, 1, 0.12)
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: browser.searching ? "No matches" : "Drop files here, or use Add file"
            font.family: Theme.font; font.pixelSize: Theme.sizeBody
            color: Theme.textMuted
        }
    }

    // Drop highlight: dashed white outline and a label
    Item {
        id: dropFrame
        anchors { fill: parent; margins: 6 }
        opacity: browser.dropActive ? 1 : 0
        visible: opacity > 0.001
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        Rectangle {
            anchors.fill: parent
            radius: 8
            color: Qt.rgba(1, 1, 1, 0.07)
        }
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Qt.rgba(1, 1, 1, 0.8)
                strokeWidth: 1.5
                fillColor: "transparent"
                strokeStyle: ShapePath.DashLine
                dashPattern: [4, 3]
                PathRectangle { x: 1; y: 1; width: dropFrame.width - 2; height: dropFrame.height - 2; radius: 8 }
            }
        }
        Row {
            anchors.centerIn: parent
            spacing: 10
            Icon { anchors.verticalCenter: parent.verticalCenter; glyph: Theme.icon.add; size: 14; color: Theme.text }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Drop to add to ‹" + browser.folderName + "›"
                font.family: Theme.font; font.pixelSize: Theme.sizeTitle; font.weight: Font.Medium
                color: Theme.text
            }
        }
    }
}
