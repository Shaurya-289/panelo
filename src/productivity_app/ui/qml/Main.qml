import QtQuick
import QtQuick.Window
import QtQuick.Effects
import App
import "App/dates.js" as Dates
import "App/smartparse.js" as Smart
import "App/recurrence.js" as Recur

Window {
    id: window
    width: 1320
    height: 820
    minimumWidth: 780
    minimumHeight: 540
    visible: false      // the shell shows it (hotkey, tray or morph)
    title: "Productivity"
    color: "#0b0c10"    // opaque: the glass look is the blurred wallpaper drawn below
    flags: Qt.Window | Qt.FramelessWindowHint

    property string section: "notes"
    property string backdropSource: ""   // wallpaper picture, set by the shell (image://snapshot/wall-N)
    property string filesRel: ""         // current folder of the Files section
    property string filesMode: Store.filesViewMode    // "list" | "grid", remembered in Settings
    readonly property bool maximized: visibility === Window.Maximized
    // read by the self-test
    readonly property bool editorOpen: editor.isOpen
    readonly property bool taskEditorOpen: taskEditor.isOpen
    readonly property var taskEditorId: taskEditor.task ? taskEditor.task.id : -1
    readonly property bool pickerOpen: datePicker.isOpen
    readonly property bool reminderEditorOpen: reminderEditor.isOpen
    readonly property var reminderEditorId: reminderEditor.reminder ? reminderEditor.reminder.id : -1
    // read by the self-test: the title-bar search
    readonly property bool searchDropdownOpen: titleSearch.dropdownOpen
    readonly property bool searchFocused: titleSearch.focused
    readonly property string searchText: titleSearch.text
    readonly property string searchCounts: JSON.stringify(titleSearch.counts)
    readonly property string searchShown: JSON.stringify(titleSearch.shownCounts)
    readonly property int searchCurrent: titleSearch.current
    readonly property int searchNavCount: titleSearch.navCount
    readonly property bool searchCollapsed: titleSearch.collapsed
    readonly property bool searchBoxShown: titleSearch.boxShown
    readonly property string searchCurrentTitle: {
        const row = titleSearch.rows.find(r => r.navIndex === titleSearch.current)
        return row ? (row.type === "more" ? row.label : row.title) : ""
    }
    // read by the self-test: the tour
    readonly property bool tourActive: tour.isOpen
    readonly property bool tourVisible: tour.visible
    readonly property int tourStep: tour.step
    readonly property string tourNameText: tour.nameBox.text
    readonly property string tourNextLabel: tour.nextLabel
    readonly property string tourStepLabel: tour.stepLabel
    readonly property string tourIntroText: tour.introText
    readonly property real tourProgress: tour.progress
    readonly property int takeNoteIconCount: takeNote.iconCount
    readonly property bool takeNoteHasChecklist: takeNote.hasChecklistIcon
    readonly property bool repeatOpen: reminderEditor.repeatOpen
    readonly property bool repeatMenuOpen: reminderEditor.repeatMenuOpen
    readonly property string repeatText: reminderEditor.repeatText
    readonly property string reminderInfoText: reminderEditor.infoText
    readonly property bool pastOpen: remindersPage.pastOpen
    readonly property bool confirmOpen: confirm.isOpen
    readonly property real backdropBlurValue: backdrop.blurAmount     // read by the self-test
    readonly property real backdropDimValue: backdrop.darkness
    readonly property bool hotkeyRecording: settingsPage.recording
    readonly property string hotkeyStatus: settingsPage.hotkeyStatus
    readonly property string filesError: settingsPage.filesError
    readonly property real settingsSizeMax: settingsPage.sizeSliderMax
    readonly property bool soundMenuOpen: settingsPage.soundMenuOpen
    // Fullscreen focus ("zen"): the shell's Controller turns zenActive on and off around showFullScreen()
    property bool zenActive: false
    property int zenCalmDelay: 3000                 // ms without mouse movement before the controls fade
    readonly property bool zenControlsShown: zen.controlsShown
    readonly property bool zenVisible: zen.visible
    readonly property bool zenResetHintShown: zen.resetHintShown
    readonly property string zenTime: zen.timeString
    readonly property int backupCount: settingsPage.backupCount
    readonly property bool settingsMenuOpen: settingsPage.menuOpen
    readonly property bool resetHintShown: focusPage.resetHintShown
    readonly property bool lengthsOpen: focusPage.lengthsOpen
    readonly property bool newTimerOpen: focusPage.newTimerOpen
    readonly property bool timerMenuOpen: focusPage.timerMenuOpen
    readonly property string lengthsText: focusPage.lengthsText
    readonly property var editorNoteId: editor.note ? editor.note.id : -1
    readonly property bool drawerOpen: drawer.open
    readonly property bool filesMenuOpen: filesBrowser.menuOpen
    readonly property bool showsNotes: section === "notes" || section === "archive"
    readonly property var visibleNotes: section === "archive"
        ? Store.notes.filter(n => !n.deletedAt && n.archived)
        : Store.notes.filter(n => !n.deletedAt && !n.archived)
    readonly property var pinnedNotes: visibleNotes.filter(n => n.pinned)
    readonly property var otherNotes: visibleNotes.filter(n => !n.pinned)

    // read by the self-test
    readonly property bool tasksDragging: tasksPage.dragging
    readonly property bool tasksLastDrop: tasksPage.lastDropOk
    readonly property bool taskMenuOpen: taskEditor.menuOpen
    readonly property string tasksPreview: tasksPage.addBar.preview

    signal shotSaved(string path)
    signal fullscreenRequested()        // the Focus page's ⛶ button; Controller.enter_fullscreen
    signal fullscreenExitRequested()    // Esc, the exit button or a double-click on the time

    // The close button and the hotkey hide the app to the tray; only "Quit" exits.
    onClosing: (close) => {
        close.accepted = false
        if (Store.closeHidesToTray) window.hide()
        else Qt.quit()
    }

    function toggleMaximize() { maximized ? window.showNormal() : window.showMaximized() }

    // The demo states below name the demo data's tasks and reminders by their titles (ids are the
    // backend's).
    function demoTask(title) { return Store.tasks.find(t => t.title === title) }
    function demoReminder(title, repeating) { return Store.reminders.find(r => r.title === title && !!r.repeat === repeating) }
    // Used by `--screenshot`: renders this window's scene only, then quits.
    function demo(state) {
        demoState = state
        if (state === "editor") editor.open(window.pinnedNotes[0], takeNote)
        else if (state === "drawer") drawer.open = true
        else if (state === "compose") takeNote.open()
        else if (state === "search-dropdown") {
            section = "notes"
            titleSearch.focusBox()
            titleSearch.text = "pr"
        }
        else if (state.startsWith("tour-")) {
            section = "notes"
            tour.goTo(Number(state.slice(5)) - 1)
            if (state === "tour-1") tour.nameBox.text = "Sam"
        }
        else if (state === "settings-name") { section = "settings"; Store.userName = "Sam" }
        else if (state === "tasks") section = "tasks"
        else if (state === "reminders-repeat") {
            section = "reminders"
            // the due-now card of a repeating reminder shows the repeat glyph too
            Store.setReminderRepeat(demoReminder("Drink water", false).id, { kind: "every", n: 2, unit: "hours" })
        }
        else if (state === "reminders") section = "reminders"
        else if (state === "trash") section = "trash"
        else if (state === "settings-top") section = "settings"
        else if (state === "settings-bottom") { section = "settings"; demoTimer.start() }
        else if (state === "settings-hotkey-recording") { section = "settings"; demoTimer.start() }
        else if (state === "settings-sound-picker" || state === "settings-appearance") { section = "settings"; demoTimer.start() }
        else if (state === "trash-empty") { section = "trash"; Store.emptyTrash() }
        else if (state === "focus") section = "focus"
        else if (state === "focus-lengths" || state === "focus-newtimer") { section = "focus"; demoTimer.start() }
        else if (state === "reminder-editor" || state === "reminder-repeat-popup") { section = "reminders"; demoTimer.start() }
        else if (state === "tasks-quickadd") {
            section = "tasks"
            tasksPage.addBar.text = "Call mom tomorrow 5pm !high"
            tasksPage.addBar.focusInput()
        }
        else if (state === "tasks-drag" || state === "task-editor" || state === "task-datepicker") {
            section = "tasks"
            demoTimer.start()
        }
        else if (state.startsWith("files-")) {
            section = "files"
            filesRel = state === "files-context" || state === "files-move" || state === "files-rename" ? "Study/Physics"
                     : state === "files-drop" ? "Study" : ""
            filesMode = state === "files-full-grid" ? "grid" : "list"
            if (state === "files-drop") filesBrowser.dropActive = true
            if (state === "files-context" || state === "files-move" || state === "files-rename") demoTimer.start()
        }
    }
    Timer {
        id: demoTimer
        interval: 200
        onTriggered: {
            if (window.demoState === "settings-bottom") settingsPage.scrollTo("bottom")
            else if (window.demoState === "settings-hotkey-recording") { settingsPage.startRecording() }
            else if (window.demoState === "settings-sound-picker") settingsPage.demoSoundPicker()
            else if (window.demoState === "settings-appearance") settingsPage.demoAppearance()
            else if (window.demoState === "focus-lengths") focusPage.demoLengths()
            else if (window.demoState === "focus-newtimer") focusPage.demoNewTimer()
            else if (window.demoState === "reminder-editor") window.openReminder(demoReminder("Prepare for standup", false), remindersPage.items["row:" + demoReminder("Prepare for standup", false).id])
            else if (window.demoState === "reminder-repeat-popup") { window.openReminderById(demoReminder("Gym", true).id); repeatPopupTimer.start() }
            else if (window.demoState === "tasks-drag") tasksPage.demoDrag(demoTask("Finish the quarterly report").id, "tomorrow")
            else if (window.demoState === "task-editor" || window.demoState === "task-datepicker") {
                window.openTask(demoTask("Finish the quarterly report"), tasksPage.rowItem(demoTask("Finish the quarterly report").id))
                if (window.demoState === "task-datepicker") datePickerTimer.start()
            }
            else if (window.demoState === "files-move") filesBrowser.demoMove(0)
            else if (window.demoState === "files-rename") filesBrowser.demoRename(0)
            else filesBrowser.demoContext(0)
        }
    }
    property string demoState: ""
    Timer {
        id: repeatPopupTimer
        interval: 700
        onTriggered: reminderEditor.demoRepeat()
    }
    Timer {
        id: datePickerTimer
        interval: 500
        onTriggered: taskEditor.probeOpen("date")
    }
    // Used by the self-test: where to click for a Files control (window coordinates).
    function centerOf(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function allCards() { return pinnedMasonry.cards().concat(othersMasonry.cards()) }
    function hoveredNotes() { return allCards().filter(c => c.hovered).length }
    function probe(name, index) {
        if (name === "menu") return centerOf(menuButton)
        const inView = (c) => { const p = centerOf(c); return p.y > 90 && p.y < window.height - 60 }
        if (name === "noteRightOfDrawer") {   // a card the open drawer's scrim lies over
            const card = allCards().find(c => centerOf(c).x > 330 && inView(c))
            return card ? centerOf(card) : null
        }
        if (name === "navFiles") return drawer.itemCenter("files")
        if (name.startsWith("search.")) return titleSearch.probe(name.slice(7), index)
        if (name.startsWith("tour.")) return tour.probe(name.slice(5))
        if (name === "titleBlank") return { x: titleSearch.x - 9, y: 22 }
        if (name === "noteOutsideSearch") {   // a note card well away from the open dropdown
            const rect = titleSearch.cardRect()
            const card = allCards().find(c => { const p = centerOf(c); return inView(c) && (!rect || p.x > rect.x + rect.width + 20 || p.y > rect.y + rect.height + 20) })
            return card ? centerOf(card) : null
        }
        if (name === "noteUnderDrawerPanel") {   // a card hidden behind the open drawer panel itself
            const card = allCards().find(c => centerOf(c).x < 230 && inView(c))
            return card ? centerOf(card) : null
        }
        if (name === "noteOutsideEditor") {   // a card away from the open editor's sheet
            const card = allCards().find(c => inView(c) && c.note.id !== window.editorNoteId
                && (Math.abs(centerOf(c).x - window.width / 2) > editor.targetW / 2 + 40))
            return card ? centerOf(card) : null
        }
        if (name === "note") return centerOf(allCards()[index])
        if (name.startsWith("settings.")) return settingsPage.probe(name.slice(9), index)
        if (name === "trashRestore") return trashPage.centerOf("restore:" + index)
        if (name === "trashForever") return trashPage.centerOf("forever:" + index)
        if (name === "trashEmpty") return trashPage.centerOf("empty")
        if (name === "confirmYes") return confirm.confirmCenter()
        if (name === "noteEditorTrash") return editor.trashCenter()
        if (name.startsWith("focus.")) return focusPage.probe(name.slice(6), index)
        if (name.startsWith("zen.")) return zen.probe(name.slice(4))
        if (name === "reminderRow") return remindersPage.centerOf("row:" + index, -200)
        if (name === "reminderSnooze") return remindersPage.centerOf("snooze:" + index)
        if (name === "reminderDone") return remindersPage.centerOf("done:" + index)
        if (name === "reminderLink") return remindersPage.centerOf("link:" + index)
        if (name === "reminderPast") return remindersPage.centerOf("pastHeader")
        if (name === "reminderAdd") return centerOf(remindersPage.addBar)
        if (name.startsWith("reminderEditor.")) return reminderEditor.probe(name.slice(15), index)
        if (name === "taskRow") return tasksPage.rowCenter(index)
        if (name === "taskHeader") return tasksPage.headerCenter(index)
        if (name === "taskCheck") return tasksPage.checkCenter(index)
        if (name === "taskAdd") return centerOf(tasksPage.addBar)
        if (name.startsWith("taskEditor.")) return taskEditor.probe(name.slice(11), index)
        if (name.startsWith("picker.")) return datePicker.probe(name.slice(7))
        return filesBrowser.probe(name, index)
    }
    // Used by the self-test: the real parser and date helpers, called through the engine.
    function settingsSlider(name, fraction) { return settingsPage.sliderPoint(name, fraction) }
    function settingsSegment(key) { return settingsPage.segmentPoint(key) }
    function requestLibraryMove(path) { settingsPage.requestMove(path) }
    function scrollTasksTop() { tasksPage.scrollTop() }
    function scrollRemindersTop() { remindersPage.scrollTop() }
    function openTaskById(id) { window.openTask(Store.task(id), tasksPage.addBar) }
    function smartCall(text, now) { return JSON.stringify(Smart.parse(text, now)) }
    function recurCall(fn, argsJson) {
        const result = Recur[fn].apply(null, JSON.parse(argsJson))
        return JSON.stringify(result === undefined ? null : result)
    }
    function openReminderById(id) { window.openReminder(Store.reminder(id), remindersPage.addBar) }
    function datesCall(fn, argsJson) {
        const result = Dates[fn].apply(null, JSON.parse(argsJson))
        return JSON.stringify(result === undefined ? null : result)
    }
    function saveShot(path) {
        contentItem.grabToImage(result => { result.saveToFile(path); window.shotSaved(path) })
    }

    // Glass base: the desktop wallpaper, blurred and darkened (like the overlay's snapshot).
    // The picture is as big as the screen and shifts as the window moves, so it stays put.
    BlurredBackdrop {
        id: backdrop
        anchors.fill: parent
        source: window.backdropSource
        sourceX: window.screen ? window.screen.virtualX - window.x : 0
        sourceY: window.screen ? window.screen.virtualY - window.y : 0
        sourceWidth: window.screen ? window.screen.width : window.width
        sourceHeight: window.screen ? window.screen.height : window.height
        blurAmount: Store.backdropBlur
        darkness: Store.backdropDim
    }

    // Everything that blurs when the editor or drawer is open.
    Item {
        id: page
        anchors.fill: parent
        readonly property real blurAmount: Math.max(editor.progress, taskEditor.progress, reminderEditor.progress, drawer.progress * 0.6, tour.progress)
        layer.enabled: blurAmount > 0.01
        layer.effect: MultiEffect {
            blurEnabled: true
            blur: page.blurAmount * 0.7
            blurMax: 48
            autoPaddingEnabled: false
        }

        // ── Title bar ───────────────────────────────────────────────
        Item {
            id: titleBar
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 44

            DragHandler {
                target: null
                onActiveChanged: if (active) window.startSystemMove()
            }
            TapHandler {
                acceptedButtons: Qt.LeftButton
                onDoubleTapped: window.toggleMaximize()
            }

            Row {
                id: titleRow
                anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                spacing: 8
                IconButton {
                    id: menuButton
                    glyph: Theme.icon.menu
                    iconSize: 15
                    width: 34; height: 32
                    onClicked: drawer.open = !drawer.open
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: ({ notes: "Notes", archive: "Archive", tasks: "Tasks", reminders: "Reminders",
                             focus: "Focus", files: "Files", trash: "Trash", settings: "Settings" })[window.section] || ""

                    font.family: Theme.font
                    font.pixelSize: Theme.sizeHeading
                    font.weight: Font.Medium
                    color: Theme.text
                }
            }

            Row {
                id: chromeRow
                anchors { right: parent.right; top: parent.top }
                height: parent.height
                component ChromeButton: Rectangle {
                    id: chrome
                    property string glyph
                    property bool closeButton: false
                    signal clicked()
                    width: 46; height: parent.height
                    color: chromeHover.hovered ? (closeButton ? "#c42b1c" : Theme.hover) : "transparent"
                    Icon {
                        anchors.centerIn: parent
                        glyph: chrome.glyph; size: 10
                        color: chromeHover.hovered ? Theme.text : Theme.textSecondary
                    }
                    HoverHandler { id: chromeHover }
                    TapHandler { onTapped: chrome.clicked() }
                }
                ChromeButton { glyph: Theme.icon.minimize; onClicked: window.showMinimized() }
                ChromeButton { glyph: window.maximized ? Theme.icon.restore : Theme.icon.maximize; onClicked: window.toggleMaximize() }
                ChromeButton { glyph: Theme.icon.close; closeButton: true; onClicked: window.close() }
            }

            // Search: centered between the section title and the window buttons, an icon when narrow.
            // It sits beside the title bar's drag area, not on it, and its own MouseArea takes the clicks.
            TitleSearch {
                id: titleSearch
                readonly property real leftEnd: titleRow.x + titleRow.width + 16
                readonly property real rightStart: chromeRow.x - 16
                readonly property real room: Math.max(0, rightStart - leftEnd)
                collapsed: window.width < 900
                width: !boxShown ? 32 : Math.min(collapsed ? 300 : 360, room)
                x: collapsed ? rightStart - width : leftEnd + (room - width) / 2
                anchors.verticalCenter: parent.verticalCenter
                onNoteChosen: (note) => { window.section = "notes"; editor.open(note, titleSearch) }
                onTaskChosen: (task) => { window.section = "tasks"; window.openTask(task, titleSearch) }
                onReminderChosen: (reminder) => { window.section = "reminders"; window.openReminder(reminder, titleSearch) }
                onFileChosen: (entry) => {
                    if (entry.isDir) {
                        window.filesRel = entry.rel
                        window.section = "files"
                    } else {
                        Library.open(entry.rel)
                    }
                }
                onShowAllChosen: (section) => window.section = section
            }
        }

        // ── Content ─────────────────────────────────────────────────
        Item {
            id: content
            anchors { left: parent.left; right: parent.right; top: titleBar.bottom; bottom: parent.bottom }

            // Notes area
            Flickable {
                id: notesArea
                anchors { left: parent.left; right: rail.left; top: parent.top; bottom: parent.bottom }
                contentHeight: notesColumn.implicitHeight + 40
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                visible: window.showsNotes

                Column {
                    id: notesColumn
                    width: Math.min(notesArea.width - 48, 1180)
                    x: (notesArea.width - width) / 2
                    y: 8
                    spacing: 14

                    TakeNoteBar {
                        id: takeNote
                        visible: window.section === "notes"
                        width: Math.min(600, parent.width)
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                    Item { width: 1; height: 6 }

                    SectionLabel { text: "Pinned"; visible: window.pinnedNotes.length > 0; leftPadding: 2 }
                    Masonry {
                        id: pinnedMasonry
                        width: parent.width
                        visible: window.pinnedNotes.length > 0
                        items: window.pinnedNotes
                        editingId: editor.note ? editor.note.id : -1
                        onNoteOpened: (note, source) => editor.open(note, source)
                    }
                    SectionLabel {
                        text: "Others"; leftPadding: 2
                        visible: window.pinnedNotes.length > 0 && window.otherNotes.length > 0
                        topPadding: 8
                    }
                    Masonry {
                        id: othersMasonry
                        width: parent.width
                        items: window.otherNotes
                        editingId: editor.note ? editor.note.id : -1
                        onNoteOpened: (note, source) => editor.open(note, source)
                    }

                    // Empty state
                    Column {
                        visible: window.visibleNotes.length === 0
                        anchors.horizontalCenter: parent.horizontalCenter
                        topPadding: 80
                        spacing: 14
                        Icon {
                            anchors.horizontalCenter: parent.horizontalCenter
                            glyph: window.section === "archive" ? Theme.icon.archive : Theme.icon.note
                            size: 72; color: Qt.rgba(1, 1, 1, 0.12)
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: window.section === "archive" ? "Your archived notes appear here" : "Notes you add appear here"
                            font.family: Theme.font; font.pixelSize: Theme.sizeTitle + 2
                            color: Theme.textMuted
                        }
                    }
                }

                // thin scroll indicator
                Rectangle {
                    visible: notesArea.contentHeight > notesArea.height
                    x: notesArea.width - 6
                    y: notesArea.visibleArea.yPosition * notesArea.height
                    width: 3; radius: 1.5
                    height: notesArea.visibleArea.heightRatio * notesArea.height
                    color: Qt.rgba(1, 1, 1, notesArea.moving ? 0.35 : 0.15)
                    Behavior on color { ColorAnimation { duration: Theme.normal } }
                }
            }

            // Files: the shared explorer on a glass card
            GlassPanel {
                id: filesCard
                visible: window.section === "files"
                anchors { left: parent.left; right: rail.left; top: parent.top; bottom: parent.bottom
                          leftMargin: 24; rightMargin: 16; topMargin: 8; bottomMargin: 16 }
                FileBrowser {
                    id: filesBrowser
                    anchors { fill: parent; topMargin: 8 }
                    rel: window.filesRel
                    mode: window.filesMode
                    showModeToggle: true
                    onNavigated: (target) => window.filesRel = target
                    onModeRequested: (m) => { window.filesMode = m; Store.filesViewMode = m }
                }
            }

            // Tasks
            TasksPage {
                id: tasksPage
                visible: window.section === "tasks"
                anchors { left: parent.left; right: rail.left; top: parent.top; bottom: parent.bottom; rightMargin: 8 }
                onTaskOpened: (task, source) => window.openTask(task, source)
            }

            // Reminders
            RemindersPage {
                id: remindersPage
                visible: window.section === "reminders"
                anchors { left: parent.left; right: rail.left; top: parent.top; bottom: parent.bottom; rightMargin: 8 }
                pickDate: window.pickDate
                onReminderOpened: (reminder, source) => window.openReminder(reminder, source)
                onLinkOpened: (link, chip) => window.openLinked(link, chip)
            }

            // Focus
            FocusPage {
                id: focusPage
                visible: window.section === "focus"
                anchors { left: parent.left; right: rail.left; top: parent.top; bottom: parent.bottom; rightMargin: 8 }
                onFullscreenRequested: window.fullscreenRequested()
            }

            // Trash
            TrashPage {
                id: trashPage
                visible: window.section === "trash"
                anchors { left: parent.left; right: rail.left; top: parent.top; bottom: parent.bottom; rightMargin: 8 }
                onEmptyRequested: confirm.ask("Empty the trash?",
                    "Everything in the Trash will be deleted forever. This can't be undone.", "Empty trash",
                    () => Store.emptyTrash())
            }

            // Settings
            SettingsPage {
                id: settingsPage
                visible: window.section === "settings"
                anchors { left: parent.left; right: rail.left; top: parent.top; bottom: parent.bottom; rightMargin: 8 }
                onAsk: (title, message, label, callback) => confirm.ask(title, message, label, callback)
            }

            // Right rail
            Flickable {
                id: rail
                anchors { right: parent.right; top: parent.top; bottom: parent.bottom; rightMargin: 16 }
                width: window.width < 1000 ? 260 : 300
                contentHeight: railColumn.implicitHeight + 24
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                Column {
                    id: railColumn
                    width: parent.width
                    y: 8
                    spacing: Theme.gap
                    FocusCard { width: parent.width; visible: window.section !== "focus" }
                    TodayCard { width: parent.width; visible: window.section !== "tasks" }
                    RemindersCard { width: parent.width }
                }
            }
        }
    }

    NavDrawer {
        id: drawer
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: parent.bottom; topMargin: 44 }
        section: window.section
        onSelected: (key) => window.section = key
    }

    NoteEditor {
        id: editor
        anchors.fill: parent
    }
    ConfirmDialog {
        id: confirm
        anchors.fill: parent
        z: 800
    }
    TaskEditor {
        id: taskEditor
        anchors.fill: parent
        pickDate: window.pickDate
    }

    ReminderEditor {
        id: reminderEditor
        anchors.fill: parent
        pickDate: window.pickDate
        onLinkRequested: (link, chip) => window.openLinked(link, chip)
    }

    // The fullscreen focus view lies over everything else in the window.
    FocusFullscreen {
        id: zen
        anchors.fill: parent
        active: window.zenActive
        calmDelay: window.zenCalmDelay
        backdropSource: window.backdropSource
        sourceX: window.screen ? window.screen.virtualX - window.x : 0
        sourceY: window.screen ? window.screen.virtualY - window.y : 0
        sourceWidth: window.screen ? window.screen.width : window.width
        sourceHeight: window.screen ? window.screen.height : window.height
        blurAmount: Store.backdropBlur
        dim: Store.backdropDim
        onExitRequested: window.fullscreenExitRequested()
    }

    // The date + time picker (Today / Tomorrow / Next week / No date, a month grid and HH:MM)
    // shared by the task pop-out and the quick-add fields.
    property var pickCallback: null
    function pickDate(item, ms, hasTime, requireTime, callback) {
        window.pickCallback = callback
        datePicker.requireTime = requireTime
        datePicker.openFor(item, ms, hasTime)
    }
    TimePickerPopup {
        id: datePicker
        mode: "full"
        anchors.fill: parent
        onPickedAt: (ms, hasTime) => {
            const callback = window.pickCallback
            window.pickCallback = null
            if (callback) callback(ms, hasTime)
        }
    }
    function openReminder(reminder, source) {
        reminderEditor.open(reminder, source)
    }
    // A reminder's link chip: open the linked task or note (the reminder pop-out gives way).
    function openLinked(link, source) {
        if (link.kind === "task") {
            const t = Store.task(link.id)
            if (!t || t.deletedAt) return
            taskEditor.open(t, source)
        } else {
            const n = Store.note(link.id)
            if (!n || n.deletedAt) return
            editor.open(n, source)
        }
        reminderEditor.dismissNow()
    }
    function openTask(task, source) {
        if (editor.isOpen) editor.close()
        taskEditor.open(task, source)
    }

    // The first-launch tour (modal, over everything but the window's own buttons)
    Tour {
        id: tour
        anchors.fill: parent
        passThrough: Qt.rect(chromeRow.x, 0, chromeRow.width, titleBar.height)
        onChangeHotkeyRequested: {
            window.section = "settings"
            Qt.callLater(settingsPage.revealHotkey)
        }
    }

    Shortcut {
        sequence: "Esc"
        onActivated: {
            if (tour.isOpen) return                 // only Skip (or Get started) closes the tour
            if (window.zenActive) window.fullscreenExitRequested()
            else if (confirm.handleEscape()) return
            else if (titleSearch.handleEscape()) return
            else if (window.section === "settings" && settingsPage.handleEscape()) return
            else if (datePicker.isOpen) datePicker.close()
            else if (window.section === "focus" && focusPage.handleEscape()) return
            else if (reminderEditor.handleEscape()) return
            else if (reminderEditor.isOpen) reminderEditor.close()
            else if (taskEditor.handleEscape()) return
            else if (taskEditor.isOpen) taskEditor.close()
            else if (editor.isOpen) editor.close()
            else if (window.section === "files" && filesBrowser.handleEscape()) return
            else if (takeNote.expanded) takeNote.commit()
            else drawer.open = false
        }
    }
    Shortcut { sequence: "Ctrl+F"; enabled: window.visible && !tour.isOpen; onActivated: titleSearch.focusBox() }
    Shortcut { sequence: "Ctrl+N"; enabled: !tour.isOpen; onActivated: { window.section = "notes"; takeNote.open() } }

    // ── Resize edges for the frameless window ───────────────────────
    component Edge: MouseArea {
        property int edges
        enabled: !window.maximized
        hoverEnabled: true
        onPressed: window.startSystemResize(edges)
    }
    readonly property int grip: 6
    Edge { edges: Qt.LeftEdge; cursorShape: Qt.SizeHorCursor; anchors { left: parent.left; top: parent.top; bottom: parent.bottom } width: window.grip }
    Edge { edges: Qt.RightEdge; cursorShape: Qt.SizeHorCursor; anchors { right: parent.right; top: parent.top; bottom: parent.bottom } width: window.grip }
    Edge { edges: Qt.TopEdge; cursorShape: Qt.SizeVerCursor; anchors { left: parent.left; right: parent.right; top: parent.top } height: 4 }
    Edge { edges: Qt.BottomEdge; cursorShape: Qt.SizeVerCursor; anchors { left: parent.left; right: parent.right; bottom: parent.bottom } height: window.grip }
    Edge { edges: Qt.LeftEdge | Qt.TopEdge; cursorShape: Qt.SizeFDiagCursor; anchors { left: parent.left; top: parent.top } width: 10; height: 10 }
    Edge { edges: Qt.RightEdge | Qt.BottomEdge; cursorShape: Qt.SizeFDiagCursor; anchors { right: parent.right; bottom: parent.bottom } width: 12; height: 12 }
    Edge { edges: Qt.LeftEdge | Qt.BottomEdge; cursorShape: Qt.SizeBDiagCursor; anchors { left: parent.left; bottom: parent.bottom } width: 10; height: 10 }
    Edge { edges: Qt.RightEdge | Qt.TopEdge; cursorShape: Qt.SizeBDiagCursor; anchors { right: parent.right; top: parent.top } width: 10; height: 4 }
}
