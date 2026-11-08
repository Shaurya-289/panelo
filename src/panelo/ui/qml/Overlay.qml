import QtQuick
import QtQuick.Window
import QtQuick.Effects
import App
import "App/layout.js" as Layout
import "App/dates.js" as Dates

// Quick Overlay: a full-screen window over a blurred, darkened snapshot of the
// screen. the shell sizes and shows it; this file owns the layout and animations.
//
// The five panels sit in a layout tree (App/layout.js). Dragging a panel's header moves it:
// onto the middle of another panel they swap, onto an edge band the panel is split in two, and
// onto a slot of the snap bar at the top a whole template is applied. Splitters resize.
Window {
    id: overlay
    width: 1280
    height: 720
    visible: false
    title: "Panelo overlay"
    color: "#0b0c10"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

    // The Files panel's current folder; the Full App opens at the same one after a morph.
    readonly property string filesRel: filesPanel.rel
    readonly property bool captureOpen: notesPanel.noteBar.expanded
    readonly property bool captureIconsVisible: notesPanel.noteBar.iconsVisible
    readonly property bool captureHasChecklist: notesPanel.noteBar.hasChecklistIcon
    readonly property bool filesMenuOpen: filesPanel.browser.menuOpen
    readonly property string renaming: filesPanel.browser.renamingRel
    readonly property bool editorOpen: editor.isOpen
    readonly property var editorNoteId: editor.note ? editor.note.id : -1
    readonly property bool pickerOpen: picker.isOpen
    readonly property bool layoutPopupOpen: layoutPopup.isOpen
    readonly property bool snapBarShown: snapBar.barShown
    readonly property real backdropBlurValue: backdropLayer.blurAmount     // read by the self-test
    readonly property real backdropDimValue: backdropLayer.darkness
    readonly property real ghostBlurValue: ghostBackdrop.blurAmount
    readonly property real ghostDimValue: ghostBackdrop.darkness
    readonly property int shownNotes: notesPanel.shown.length
    readonly property int shownTasks: todoPanel.shown.length

    // Set by the shell before show()
    property string backdropSource: ""
    property string wallSource: ""     // the Full App's wallpaper picture (the morph ghost)

    property real progress: 0          // entrance / exit, 0..1
    property real morph: 0             // morph into the Full App, 0..1
    property rect morphTarget: Qt.rect(0, 0, 0, 0)   // the Full App's rectangle, in overlay coordinates
    property bool morphMaximized: false              // the Full App will open maximized (square corners)
    property string morphSection: "notes"
    property bool closing: false
    property bool morphing: false
    readonly property bool busy: closing || morphing
    readonly property real gap: Theme.gap
    readonly property real minPanelW: 220
    readonly property real minPanelH: 160

    signal expandRequested(string section)
    signal closeRequested()
    signal closeFinished()
    signal morphFinished(string section)
    signal shotSaved(string path)

    // Only the current layout is remembered (as JSON, in the saved settings).

    // The panel group is centered.
    // Its share of the screen is the "Overlay size" setting (60..90%), raised when the layout would
    // otherwise push a panel below the minimum size.
    readonly property real sizeFraction: {
        const wanted = Store.overlaySize
        const fitsAt = (frac) => Layout.fits(tree, Qt.rect(0, 0, width * frac, height * frac), gap, minPanelW, minPanelH)
        if (fitsAt(wanted))
            return wanted
        let low = wanted, high = Store.overlaySizeMax
        if (!fitsAt(high))
            return high
        for (let i = 0; i < 8; i++) {
            const mid = (low + high) / 2
            if (fitsAt(mid)) high = mid
            else low = mid
        }
        return high
    }
    readonly property real groupW: width * sizeFraction
    readonly property real groupH: height * sizeFraction
    readonly property rect baseRect: Qt.rect((width - groupW) / 2, (height - groupH) / 2, groupW, groupH)

    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }
    function mix(from, to) { return from + (to - from) * morph }
    // Where the Full App will be (the shell sends it before the overlay shows).
    function setTarget(rect) {
        morphTarget = Qt.rect(rect.x, rect.y, rect.width, rect.height)
        morphMaximized = !!rect.maximized
    }

    // ── Layout state ────────────────────────────────────────────────
    property var tree: Layout.defaultTree()
    readonly property var groupRect: Qt.rect(0, 0, group.width, group.height)
    readonly property var computed: Layout.computeRects(tree, groupRect, gap)
    readonly property var rects: computed.rects
    readonly property string layoutText: JSON.stringify(tree)
    readonly property string activeTemplate: Layout.activeTemplate(tree)
    // Which templates fit the screen with the current panel order (others are greyed out).
    readonly property var templateFit: {
        const order = Layout.rankByArea(rects)
        const out = {}
        for (const name of Layout.TEMPLATES)
            out[name] = Layout.fits(Layout.applyTemplate(name, order), groupRect, gap, minPanelW, minPanelH)
        return out
    }

    property bool resizing: false      // a splitter is being dragged: no gliding
    property bool settling: false      // a drop just happened: panels are gliding into place
    // Panels glide to new rectangles, except while resizing, morphing or entering.
    readonly property bool glideOk: !resizing && !morphing && !closing && progress > 0.99

    Component.onCompleted: tree = Layout.parse(Store.overlayLayoutJson)

    function commitLayout() { Store.overlayLayoutJson = JSON.stringify(tree) }
    function resetLayout() { tree = Layout.defaultTree(); commitLayout() }
    function applyTemplateNamed(name) {
        if (templateFit[name] === false)
            return false
        tree = Layout.applyTemplate(name, Layout.rankByArea(rects))
        commitLayout()
        return true
    }
    function loadLayout(json) { tree = Layout.parse(json) }

    // ── Drag and drop of panels ─────────────────────────────────────
    readonly property var panelItems: ({
        notes: notesPanel, todo: todoPanel, files: filesPanel, timers: timersPanel, reminders: remindersPanel
    })
    property bool dragging: false
    property string dragKey: ""
    property point dragOrigin               // the press, in `panels` coordinates
    property point dragStart                // the panel's slot origin when it was lifted
    property var snapDrop: null             // slot of the snap bar under the cursor
    property var previewTree: null          // the layout a drop here would produce
    property var previewMain: null          // where the dragged panel would land
    property var previewSecond: null        // where the target panel would end up
    property var lastMain: null
    property var lastSecond: null
    property bool previewValid: true
    property string previewKind: ""         // "swap" | "split" | ""
    property string previewZone: ""
    property string previewTarget: ""
    onPreviewMainChanged: if (previewMain) lastMain = previewMain
    onPreviewSecondChanged: if (previewSecond) lastSecond = previewSecond

    function clearPreview() {
        previewTree = null; previewMain = null; previewSecond = null
        previewKind = ""; previewZone = ""; previewTarget = ""; previewValid = true
    }
    function beginDrag(key, sx, sy) {
        if (busy || settling || dragging || resizing) {
            panelItems[key].abortDrag()
            return
        }
        notesPanel.collapseCapture()
        filesPanel.browser.handleEscape()
        layoutPopup.close()
        picker.close()
        const item = panelItems[key], r = rects[key]
        dragKey = key
        dragging = true
        dragOrigin = panels.mapFromItem(null, sx, sy)
        dragStart = Qt.point(r.x, r.y)
        clearPreview()
        snapDrop = null
        item.glide = false
        item.liftX = r.x; item.liftY = r.y; item.liftWidth = r.width; item.liftHeight = r.height
        item.following = true
        item.lifted = true
    }
    function updateDrag(sx, sy) {
        if (!dragging)
            return
        const item = panelItems[dragKey]
        const p = panels.mapFromItem(null, sx, sy)
        item.liftX = dragStart.x + p.x - dragOrigin.x
        item.liftY = dragStart.y + p.y - dragOrigin.y
        // The snap bar has priority over the panels beneath it.
        const slot = snapBar.hit(sx, sy)
        snapDrop = slot
        snapBar.hoverTemplate = slot && !slot.disabled ? slot.tpl : -1
        snapBar.hoverSlot = slot && !slot.disabled ? slot.slot : -1
        if (slot || snapBar.containsScene(sx, sy)) {
            clearPreview()
            return
        }
        const target = Layout.panelAt(rects, p.x, p.y, dragKey)
        if (target === "") {
            clearPreview()
            return
        }
        updatePreview(target, Layout.zoneAt(rects[target], p.x, p.y))
    }
    function updatePreview(target, zone) {
        if (zone === "center") {
            previewTree = Layout.swap(tree, dragKey, target)
            previewValid = true
            previewMain = rects[target]
            previewSecond = rects[dragKey]
            previewKind = "swap"
        } else {
            const t = Layout.splitAt(tree, target, dragKey, zone)
            if (!t) {
                clearPreview()
                return
            }
            const after = Layout.computeRects(t, groupRect, gap).rects
            previewTree = t
            previewValid = Layout.fits(t, groupRect, gap, minPanelW, minPanelH)
            previewMain = after[dragKey]
            previewSecond = after[target]
            previewKind = "split"
        }
        previewZone = zone
        previewTarget = target
    }
    function endDrag(sx, sy) {
        if (!dragging)
            return
        updateDrag(sx, sy)
        let next = null
        if (snapDrop) {
            if (!snapDrop.disabled) {
                const order = Layout.orderForSlot(Layout.rankByArea(rects), dragKey, snapDrop.slot + 1)
                next = Layout.applyTemplate(snapDrop.name, order)
            }
        } else if (previewTree && previewValid) {
            next = previewTree
        }
        finishDrag(next)
    }
    // Esc, or anything that must end a drag: the panel glides back.
    function cancelDrag() {
        if (!dragging)
            return false
        panelItems[dragKey].abortDrag()
        finishDrag(null)
        return true
    }
    // `next` is the new layout (or null to cancel). The panel glides from where it is to its slot.
    function finishDrag(next) {
        const item = panelItems[dragKey]
        if (next) {
            tree = next
            commitLayout()
        }
        item.glide = true          // before the geometry stops following the cursor
        item.following = false
        item.lifted = false
        dragging = false
        dragKey = ""
        snapDrop = null
        snapBar.hoverTemplate = -1; snapBar.hoverSlot = -1
        clearPreview()
        settling = true
        settleTimer.restart()
    }
    Timer { id: settleTimer; interval: 260; onTriggered: overlay.settling = false }

    // ── Splitters ───────────────────────────────────────────────────
    property var resizeStart: null
    function beginResize(d) {
        if (!d || dragging)
            return
        resizeStart = { tree: Layout.clone(tree), path: d.path, index: d.index, avail: d.avail }
        resizing = true
    }
    function resizeBy(total) {
        if (!resizeStart)
            return
        tree = Layout.resize(resizeStart.tree, resizeStart.path, resizeStart.index, total / resizeStart.avail,
                             groupRect, gap, minPanelW, minPanelH)
    }
    function endResize() {
        if (!resizing)
            return
        resizing = false
        resizeStart = null
        commitLayout()
    }

    // ── Lifecycle (called from the shell) ─────────────────────────────
    function prepare() {
        openAnim.stop(); closeAnim.stop(); morphAnim.stop()
        cancelDrag()
        settling = false; resizing = false
        progress = 0; morph = 0
        closing = false; morphing = false
        search.text = ""
        editor.note = null; editor.progress = 0
        picker.isOpen = false
        layoutPopup.isOpen = false
        remindersPanel.draft = ""
        timersPanel.index = 0
        notesPanel.noteBar.expanded = false
        notesPanel.noteBar.titleText = ""
        notesPanel.noteBar.bodyText = ""
    }
    function beginOpen() {
        Store.refreshClock()
        closing = false
        openAnim.restart()
        search.input.forceActiveFocus()
    }
    function beginClose() {
        if (closing || morphing)
            return
        cancelDrag()
        closing = true
        openAnim.stop()
        editor.note = null; editor.progress = 0
        picker.isOpen = false
        layoutPopup.isOpen = false
        closeAnim.restart()
    }
    function morphTo(section, rect) {
        if (morphing)
            return
        cancelDrag()
        notesPanel.collapseCapture()
        filesPanel.browser.handleEscape()
        editor.note = null; editor.progress = 0
        picker.isOpen = false
        layoutPopup.isOpen = false
        morphSection = section
        setTarget(rect)
        morphing = true
        morphAnim.restart()
    }

    // Used by `--screenshot`
    property string demoState: ""
    function demo(state) {
        demoState = state
        if (state === "overlay-search") {
            search.text = "re"
        } else if (state === "overlay-editor") {
            editor.open(Store.notes[0], notesPanel.rowAt(0))
        } else if (state === "overlay-compose") {
            notesPanel.noteBar.open()
            notesPanel.noteBar.titleText = "Physics lab report"
            notesPanel.noteBar.bodyText = "Compare the measured acceleration with g.
Include the velocity-time graph and the error bars."
        } else if (state === "files-overlay") {
            filesPanel.rel = "Study/Physics"
        } else if (state === "files-search") {
            search.text = "ph"
        } else if (state === "overlay-morph") {
            setTarget({ x: (width - 1320) / 2, y: (height - 820) / 2, width: 1320, height: 820 })
            morphSection = "notes"
            morph = 0.5
        } else if (state === "overlay-carousel") {
            timersPanel.goTo(1)
        } else if (state === "timepicker") {
            remindersPanel.draft = "Call mom"
            picker.openFor(remindersPanel.clockButton)
        } else if (state === "layout-picker") {
            layoutPopup.openFor(layoutButton)
        } else if (state === "overlay-greeting") {
            Store.userName = "Sam"
            Store.clockOverride = Dates.dayAt(0, 9, 41)
        } else if (state === "overlay-greeting-12h") {
            Store.userName = "Sam"
            Store.clockOverride = Dates.dayAt(0, 15, 7)
        } else if (state === "layout-big") {
            applyTemplateNamed("focus")
        } else if (state === "layout-rows") {
            applyTemplateNamed("rows")
        } else if (state === "layout-columns") {
            applyTemplateNamed("columns")
        } else if (state === "layout-drag-swap") {
            demoDrag("notes", "timers", 0.5, 0.55)
        } else if (state === "layout-drag-split") {
            demoDrag("todo", "files", 0.97, 0.5)
        } else if (state === "layout-drag-invalid") {
            applyTemplateNamed("columns")
            demoTimer.start()
        } else if (state === "layout-topbar") {
            const at = headerPoint("todo")
            beginDrag("todo", at.x, at.y)
            demoTimer.start()
        }
    }
    Timer {
        id: demoTimer
        interval: 500
        onTriggered: {
            if (overlay.demoState === "layout-invalid" || overlay.demoState === "layout-drag-invalid")
                overlay.demoDrag("notes", "reminders", 0.985, 0.5)
            else if (overlay.demoState === "layout-topbar") {
                const p = overlay.snapSlotPoint(1, 0)
                overlay.updateDrag(p.x, p.y)
            }
        }
    }
    // A drag without a mouse: lift `key` by its header and hold it at a fraction of `target`.
    function demoDrag(key, target, fx, fy) {
        const from = headerPoint(key)
        beginDrag(key, from.x, from.y)
        const to = pointIn(target, fx, fy)
        updateDrag(to.x, to.y)
    }

    // Used by the self-test: the greeting's, the clock's, the search bar's and the panel group's rectangles
    function cornerGeometry() {
        const r = (item) => ({ x: item.x, y: item.y, width: item.width, height: item.height })
        return JSON.stringify({ greeting: r(greeting), clock: r(clockRow), search: r(search), base: r(baseRect) })
    }
    // Used by the self-test: where to click (window coordinates) for a named control.
    function pointIn(key, fx, fy) {
        const item = panelItems[key]
        const p = item.mapToItem(null, item.width * fx, item.height * fy)
        return { x: p.x, y: p.y }
    }
    function headerPoint(key) {
        const item = panelItems[key]
        const p = item.mapToItem(null, Math.min(item.width * 0.35, 140), item.headerHeight / 2)
        return { x: p.x, y: p.y }
    }
    function snapSlotPoint(tpl, slot) {
        const cell = snapBar.cellAt(tpl)
        const r = cell.thumb.slots[slot]
        const p = cell.thumb.mapToItem(null, r.x + r.width / 2, r.y + r.height / 2)
        return { x: p.x, y: p.y }
    }
    function panelGeometry(key) {
        const item = panelItems[key]
        return JSON.stringify({ x: item.x, y: item.y, width: item.width, height: item.height })
    }
    function layoutCall(fn, argsJson) {
        const result = Layout[fn].apply(null, JSON.parse(argsJson))
        return JSON.stringify(result === undefined ? null : result)
    }
    readonly property string rectsText: JSON.stringify(rects)
    function probe(name, index) {
        let item = null
        if (name === "noteBar") item = notesPanel.noteBar
        else if (name === "noteTitle") {
            const t = notesPanel.noteBar.mapToItem(null, notesPanel.noteBar.width / 2, 22)
            return { x: t.x, y: t.y }
        }
        else if (name === "noteRow") item = notesPanel.rowAt(index)
        else if (name === "todoCheck") item = todoPanel.checkAt(index)
        else if (name === "todoHeader") {
            const p = todoPanel.mapToItem(null, todoPanel.width / 2, 20)
            return { x: p.x, y: p.y }
        } else if (name === "layoutButton") item = layoutButton
        else if (name === "layoutThumb") item = layoutPopup.cellAt(index).thumb
        else if (name === "layoutReset") {
            const p = layoutPopup.card.mapToItem(null, layoutPopup.card.width / 2, layoutPopup.card.height - 28)
            return { x: p.x, y: p.y }
        } else if (name === "layoutCardOutside") {   // inside the popup card, between the thumbnails
            const p = layoutPopup.card.mapToItem(null, layoutPopup.card.width / 2, 20)
            return { x: p.x, y: p.y }
        } else if (name === "splitterRoot") {
            const d = computed.splitters.find(s => s.path.length === 0 && s.index === index)
            const p = panels.mapToItem(null, d.rect.x + d.rect.width / 2, d.rect.y + d.rect.height / 2)
            return { x: p.x, y: p.y }
        } else return filesPanel.browser.probe(name, index)
        const q = item.mapToItem(null, item.width / 2, Math.min(14, item.height / 2))
        return { x: q.x, y: q.y }
    }
    // Used by the self-test: types a note into the capture box and saves it.
    function captureNote(title, body) { notesPanel.noteBar.capture(title, body) }
    function saveShot(path) {
        contentItem.grabToImage(result => { result.saveToFile(path); overlay.shotSaved(path) })
    }

    NumberAnimation { id: openAnim; target: overlay; property: "progress"; to: 1; duration: 180; easing.type: Easing.OutCubic }
    SequentialAnimation {
        id: closeAnim
        NumberAnimation { target: overlay; property: "progress"; to: 0; duration: 120; easing.type: Easing.OutCubic }
        ScriptAction { script: { overlay.closing = false; overlay.closeFinished() } }
    }
    SequentialAnimation {
        id: morphAnim
        NumberAnimation { target: overlay; property: "morph"; from: 0; to: 1; duration: 320; easing.type: Easing.OutCubic }
        ScriptAction { script: overlay.morphFinished(overlay.morphSection) }
    }

    // ── Background: blurred, darkened snapshot ──────────────────────
    // Static: the blur and the darkness never change. During the morph the sharp snapshot
    // crossfades in on top (an opacity change only), instead of animating the blur radius,
    // which would re-run a full-screen blur every frame.
    BlurredBackdrop {
        id: backdropLayer
        anchors.fill: parent
        opacity: overlay.progress
        source: overlay.backdropSource
        // bleed past the edges so the blur has pixels to sample
        sourceX: -80; sourceY: -80
        sourceWidth: overlay.width + 160; sourceHeight: overlay.height + 160
        blurAmount: Store.backdropBlur      // Settings > Appearance, live
        darkness: Store.backdropDim
    }
    Image {
        anchors.fill: parent
        source: overlay.backdropSource
        cache: false
        // never fully transparent, so the texture is uploaded while the overlay opens
        // (0.2% opacity is invisible) and the morph's first frame has nothing to prepare
        opacity: overlay.progress * Math.max(0.002, overlay.morph)
    }

    // ── Search and the layout button ────────────────────────────────
    // Both fade out while a panel is dragged: the snap bar takes their place at the top.
    SearchField {
        id: search
        width: Math.min(440, overlay.width * 0.6)
        height: 40
        x: (overlay.width - width) / 2
        y: Math.max(10, overlay.baseRect.y - height - 14)
        opacity: overlay.progress * (1 - Math.min(1, overlay.morph * 2)) * (1 - snapBar.shown)
        enabled: !overlay.busy && !overlay.dragging
    }
    GlassPanel {
        id: layoutButtonCard
        width: 40; height: 40
        x: search.x + search.width + 8
        y: search.y
        fill: Qt.rgba(0.11, 0.115, 0.135, 0.72)
        hovered: layoutPopup.isOpen
        opacity: search.opacity
        IconButton {
            id: layoutButton
            anchors.fill: parent
            anchors.margins: 1
            glyph: Theme.icon.layout
            iconSize: 16
            active: layoutPopup.isOpen
            enabled: !overlay.busy && !overlay.dragging
            onClicked: {
                notesPanel.collapseCapture()
                if (layoutPopup.isOpen) layoutPopup.close()
                else layoutPopup.openFor(layoutButton)
            }
        }
    }

    // ── Greeting (top left) and clock (top right) ───────────────────
    // Both line up with the panel group's edges and with the search bar's middle, fade in with the
    // overlay and fade out while a panel is dragged (and during the morph), like the search bar.
    // A narrow screen hides the greeting first, then the clock, instead of letting them touch the search bar.
    readonly property real cornerFade: overlay.progress * (1 - Math.min(1, overlay.morph * 2)) * (1 - snapBar.shown)
    readonly property real greetingRight: baseRect.x + greeting.implicitWidth
    readonly property bool greetingFits: greetingRight + 28 <= search.x
    readonly property real clockLeft: baseRect.x + baseRect.width - clockRow.width
    readonly property bool clockFits: clockLeft - 28 >= layoutButtonCard.x + layoutButtonCard.width
    readonly property string greetingTextShown: greeting.text           // read by the self-test
    readonly property real greetingOpacity: greeting.opacity
    readonly property string clockTextShown: clockTime.text
    readonly property string clockSuffixShown: clockSuffix.text
    readonly property real clockOpacity: clockRow.opacity
    Text {
        id: greeting
        x: overlay.baseRect.x
        y: search.y + (search.height - height) / 2
        text: Store.greetingText
        font.family: Theme.font; font.pixelSize: 22; font.weight: Font.Light
        color: Theme.text
        opacity: overlay.greetingFits ? 0.9 * overlay.cornerFade : 0
        visible: opacity > 0.01
    }
    Item {
        id: clockRow
        width: clockTime.width + (clockSuffix.visible ? 6 + clockSuffix.width : 0)
        height: clockTime.height
        x: overlay.baseRect.x + overlay.baseRect.width - width
        y: search.y + (search.height - height) / 2
        opacity: overlay.clockFits ? 0.9 * overlay.cornerFade : 0
        visible: opacity > 0.01
        Text {
            id: clockTime
            text: Store.clockParts.text
            font.family: Theme.font; font.pixelSize: 22; font.weight: Font.Light
            font.features: ({ "tnum": 1 })     // equal-width digits, so the clock doesn't jiggle when a minute changes
            color: Theme.text
        }
        Text {
            id: clockSuffix
            visible: text !== ""
            x: clockTime.width + 6
            y: clockTime.baselineOffset - baselineOffset
            text: Store.clockParts.suffix
            font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Normal
            color: Theme.textMuted
        }
    }

    // ── Morph ghost: the Full App's window ──────────────────────────
    // It looks like the Full App (blurred wallpaper + darkness), so the hand-over is seamless.
    // It is built once at the final size, cached as a texture and kept "warm" at a tiny
    // opacity from the entrance on; the morph only moves, scales and fades it.
    readonly property real ghostOpacity: clamp((morph - 0.15) / 0.6, 0, 1)
    readonly property real ghostRadius: morphMaximized ? 0 : 8
    Item {
        id: ghostBox
        readonly property real tw: Math.max(1, overlay.morphTarget.width)
        readonly property real th: Math.max(1, overlay.morphTarget.height)
        x: overlay.mix(overlay.baseRect.x, overlay.morphTarget.x)
        y: overlay.mix(overlay.baseRect.y, overlay.morphTarget.y)
        width: tw; height: th
        opacity: Math.max(0.002, overlay.ghostOpacity) * overlay.progress
        transform: Scale {
            origin.x: 0; origin.y: 0
            xScale: overlay.mix(overlay.baseRect.width / ghostBox.tw, 1)
            yScale: overlay.mix(overlay.baseRect.height / ghostBox.th, 1)
        }
        Item {
            anchors.fill: parent
            layer.enabled: true
            layer.smooth: true
            layer.effect: MultiEffect {
                maskEnabled: true
                maskSource: ghostMask
            }
            BlurredBackdrop {
                id: ghostBackdrop
                anchors.fill: parent
                source: overlay.wallSource
                sourceX: -overlay.morphTarget.x; sourceY: -overlay.morphTarget.y
                sourceWidth: overlay.width; sourceHeight: overlay.height
                blurAmount: Store.backdropBlur      // the same glass as the Full App
                darkness: Store.backdropDim
            }
        }
        Item {
            id: ghostMask
            anchors.fill: parent
            visible: false
            layer.enabled: true
            Rectangle { anchors.fill: parent; radius: overlay.ghostRadius }
        }
        Rectangle {
            anchors.fill: parent
            radius: overlay.ghostRadius
            color: "transparent"
            border.width: overlay.morphMaximized ? 0 : 1
            border.color: Theme.border
        }
    }

    // ── Panel group ─────────────────────────────────────────────────
    // The group keeps its size (so no panel ever re-lays out). While morphing, the panels are
    // a cached texture, and the group is moved and scaled (a transform) towards the target.
    Item {
        id: group
        x: overlay.mix(overlay.baseRect.x, overlay.morphTarget.x)
        y: overlay.mix(overlay.baseRect.y, overlay.morphTarget.y)
        width: overlay.baseRect.width
        height: overlay.baseRect.height
        opacity: overlay.progress
        scale: 0.97 + 0.03 * overlay.progress
        transform: Scale {
            origin.x: 0; origin.y: 0
            xScale: overlay.mix(1, overlay.morphTarget.width / Math.max(1, overlay.baseRect.width))
            yScale: overlay.mix(1, overlay.morphTarget.height / Math.max(1, overlay.baseRect.height))
        }

        Item {
            id: panels
            anchors.fill: parent
            opacity: 1 - overlay.clamp(overlay.morph / 0.6, 0, 1)
            enabled: !overlay.busy

            // Blurred behind the editor; cached as one texture while morphing (so it can be
            // scaled and faded without being redrawn).
            readonly property real blurAmount: editor.progress
            layer.enabled: blurAmount > 0.01 || overlay.morphing
            layer.smooth: true
            layer.effect: blurAmount > 0.01 ? blurEffect : null
            Component {
                id: blurEffect
                MultiEffect {
                    blurEnabled: true
                    blur: panels.blurAmount * 0.7
                    blurMax: 48
                    autoPaddingEnabled: false
                }
            }

            // Where the dragged panel came from: a faint empty outline until the drop.
            Rectangle {
                visible: overlay.dragging && overlay.rects[overlay.dragKey] !== undefined
                x: overlay.dragging ? overlay.rects[overlay.dragKey].x : 0
                y: overlay.dragging ? overlay.rects[overlay.dragKey].y : 0
                width: overlay.dragging ? overlay.rects[overlay.dragKey].width : 0
                height: overlay.dragging ? overlay.rects[overlay.dragKey].height : 0
                radius: Theme.radius
                color: Qt.rgba(0, 0, 0, 0.10)
                border.width: 1
                border.color: Qt.rgba(1, 1, 1, 0.16)
            }

            NotesListPanel {
                id: notesPanel
                panelKey: "notes"
                slot: overlay.rects.notes
                animateGeometry: overlay.glideOk
                enabled: !overlay.dragging || overlay.dragKey === "notes"
                query: search.text
                onDragStarted: (sx, sy) => overlay.beginDrag("notes", sx, sy)
                onDragMoved: (sx, sy) => overlay.updateDrag(sx, sy)
                onDragEnded: (sx, sy) => overlay.endDrag(sx, sy)
                onNoteOpened: (note, source) => editor.open(note, source)
                onCreateRequested: (text) => {
                    Store.addNote(text, "", "none")
                    editor.open(Store.notes[0], notesPanel.rowAt(0))
                }
                onExpandRequested: overlay.expandRequested("notes")
            }
            TodoPanel {
                id: todoPanel
                panelKey: "todo"
                slot: overlay.rects.todo
                animateGeometry: overlay.glideOk
                enabled: !overlay.dragging || overlay.dragKey === "todo"
                query: search.text
                onDragStarted: (sx, sy) => overlay.beginDrag("todo", sx, sy)
                onDragMoved: (sx, sy) => overlay.updateDrag(sx, sy)
                onDragEnded: (sx, sy) => overlay.endDrag(sx, sy)
                onExpandRequested: overlay.expandRequested("tasks")
            }
            FilesPanel {
                id: filesPanel
                panelKey: "files"
                slot: overlay.rects.files
                animateGeometry: overlay.glideOk
                enabled: !overlay.dragging || overlay.dragKey === "files"
                query: search.text
                onDragStarted: (sx, sy) => overlay.beginDrag("files", sx, sy)
                onDragMoved: (sx, sy) => overlay.updateDrag(sx, sy)
                onDragEnded: (sx, sy) => overlay.endDrag(sx, sy)
                onClearQueryRequested: search.text = ""
                // an opened file's app (or Explorer) must not hide behind the always-on-top overlay
                onLaunched: overlay.closeRequested()
                onExpandRequested: overlay.expandRequested("files")
            }
            TimersPanel {
                id: timersPanel
                panelKey: "timers"
                slot: overlay.rects.timers
                animateGeometry: overlay.glideOk
                enabled: !overlay.dragging || overlay.dragKey === "timers"
                onDragStarted: (sx, sy) => overlay.beginDrag("timers", sx, sy)
                onDragMoved: (sx, sy) => overlay.updateDrag(sx, sy)
                onDragEnded: (sx, sy) => overlay.endDrag(sx, sy)
                onExpandRequested: overlay.expandRequested("focus")
            }
            RemindersPanel {
                id: remindersPanel
                panelKey: "reminders"
                slot: overlay.rects.reminders
                animateGeometry: overlay.glideOk
                enabled: !overlay.dragging || overlay.dragKey === "reminders"
                query: search.text
                onDragStarted: (sx, sy) => overlay.beginDrag("reminders", sx, sy)
                onDragMoved: (sx, sy) => overlay.updateDrag(sx, sy)
                onDragEnded: (sx, sy) => overlay.endDrag(sx, sy)
                onPickerRequested: (anchor) => picker.openFor(anchor)
                onExpandRequested: overlay.expandRequested("reminders")
            }

            // Snap preview: the exact rectangle the dragged panel will take (a translucent white
            // card, 1.5px white border) and a smaller outline where the target panel will end up.
            // Muted red when the drop would break the minimum size (and is then refused).
            Rectangle {
                z: 20
                readonly property var r: overlay.lastMain
                visible: opacity > 0.01 && r !== null
                opacity: overlay.previewMain ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                x: r ? r.x : 0; y: r ? r.y : 0
                width: r ? r.width : 0; height: r ? r.height : 0
                radius: Theme.radius
                color: overlay.previewValid ? Qt.rgba(1, 1, 1, 0.16)
                     : Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.22)
                border.width: 1.5
                border.color: overlay.previewValid ? Qt.rgba(1, 1, 1, 0.92)
                            : Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.9)
            }
            Rectangle {
                z: 20
                readonly property var r: overlay.lastSecond
                visible: opacity > 0.01 && r !== null
                opacity: overlay.previewSecond ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                x: r ? r.x + 8 : 0; y: r ? r.y + 8 : 0
                width: r ? Math.max(0, r.width - 16) : 0; height: r ? Math.max(0, r.height - 16) : 0
                radius: Math.max(2, Theme.radius - 2)
                color: "transparent"
                border.width: 1.5
                border.color: overlay.previewValid ? Qt.rgba(1, 1, 1, 0.55)
                            : Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.6)
            }

            // Splitters live in the gaps between panels. The model is just a count, so the
            // delegates survive while a splitter is dragged (the descriptors change, not the count).
            Repeater {
                model: overlay.computed.splitters.length
                Splitter {
                    id: splitter
                    required property int index
                    readonly property var d: overlay.computed.splitters[index]
                    z: 10
                    horizontal: d ? d.dir === "row" : true
                    x: d ? d.rect.x : 0
                    y: d ? d.rect.y : 0
                    width: d ? d.rect.width : 0
                    height: d ? d.rect.height : 0
                    enabled: !overlay.dragging
                    onPressedDown: overlay.beginResize(splitter.d)
                    onDragged: (total) => overlay.resizeBy(total)
                    onPressedChanged: if (!pressed) overlay.endResize()
                }
            }
        }
    }

    // ── Snap bar: layout templates at the top of the screen while a panel is dragged ──
    LayoutPicker {
        id: snapBar
        bar: true
        barShown: overlay.dragging
        fitMap: overlay.templateFit
        activeName: overlay.activeTemplate
        width: card.width; height: card.height
        x: (overlay.width - width) / 2
        y: 12 - (1 - shown) * (height + 12)
    }

    // ── Pop-out note editor and time picker (above everything) ──────
    NoteEditor {
        id: editor
        anchors.fill: parent
    }
    TimePickerPopup {
        id: picker
        anchors.fill: parent
        ready: remindersPanel.draft.trim() !== ""
        onPickedAt: (ms, hasTime) => remindersPanel.addWhen(ms)
    }

    // While the "Take a note" box is open, a click anywhere else in the overlay saves and
    // collapses it, and is swallowed: whatever lies underneath (a note row, a checkbox...) does
    // not react to that click. The layer is only active while the box is open and its
    // containment mask excludes the box itself, so the box's text fields still get their clicks.
    ClickBlocker {
        id: captureAway
        anchors.fill: parent
        enabled: overlay.captureOpen
        dismissOnPress: false
        containmentMask: QtObject {
            function contains(point: point): bool {
                const p = captureAway.mapToItem(notesPanel.noteBar, point.x, point.y)
                return !notesPanel.noteBar.contains(p)
            }
        }
        onDismissed: notesPanel.collapseCapture()
    }

    // The layout picker popup (the button next to the search bar). Its click-away layer swallows
    // the click that closes it.
    LayoutPicker {
        id: layoutPopup
        anchors.fill: parent
        fitMap: overlay.templateFit
        activeName: overlay.activeTemplate
        onTemplateChosen: (name) => { overlay.applyTemplateNamed(name); layoutPopup.close() }
        onResetRequested: { overlay.resetLayout(); layoutPopup.close() }
    }

    // Esc: a drag, then the editor, the pickers, the search, then close.
    Shortcut {
        sequence: "Esc"
        enabled: overlay.visible && !overlay.busy
        onActivated: {
            if (overlay.cancelDrag()) return
            if (editor.isOpen) editor.close()
            else if (layoutPopup.isOpen) layoutPopup.close()
            else if (picker.isOpen) picker.close()
            else if (filesPanel.browser.handleEscape()) return
            else if (notesPanel.collapseCapture()) return
            else if (search.text !== "") search.text = ""
            else overlay.closeRequested()
        }
    }
}
