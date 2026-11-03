pragma Singleton
import QtQuick
import "dates.js" as Dates
import "smartparse.js" as Smart
import "recurrence.js" as Recur

// The data the pages read, and the actions they call. This is a thin facade over the Python
// `Backend` (the context property of the same name): the lists come from the real services as
// JSON, every action is a Backend call, and the settings are mirrors that save on assignment.
// What stays here is what only the screen needs: the clock, the grouped and memoized views
// (taskGroups, reminderGroups, ...: they change only when their content does, so a list that
// is being dragged is never rebuilt by the clock), and the countdown numbers, which tick once
// a second from the deadlines the Backend reports.
QtObject {
    id: store

    // ── Clock ────────────────────────────────────────────────────────
    property real now: Date.now()                                   // updated every second
    property real minute: Math.floor(Date.now() / 60000) * 60000    // updated once a minute (relative texts)

    // ── Settings: mirrors of the saved settings; assigning one saves it ──
    property bool startWithWindows: false
    property bool closeHidesToTray: true
    property string hotkey: "Ctrl+Alt+Space"
    property int focusMs: 25 * 60000
    property int shortMs: 5 * 60000
    property int longMs: 15 * 60000
    property bool reminderAlerts: true
    property string notificationStyle: "toast"     // "toast" | "windows" | "both"
    property string notificationSound: ""          // file path of the toast sound, "" = none
    property real backdropBlur: 1.0
    property real backdropDim: 0.45
    readonly property real overlaySizeMax: 0.90
    property real overlaySize: 0.85
    property bool autoBackup: true
    property int keepBackups: 7
    property string libraryPath: ""
    property string userName: ""                   // asked by the first-launch tour; the overlay greets with it
    property bool tourDone: false                  // the first-launch tour has been finished or skipped
    property string overlayLayoutJson: ""          // the overlay's panel layout
    property string filesViewMode: "list"          // the Files page: "list" | "grid"

    property bool _loadingSettings: false
    function _push(key, value) {
        if (!_loadingSettings)
            Backend.setSetting(key, value)
    }
    function _loadSettings() {
        const saved = JSON.parse(Backend.settingsJson)
        if (!saved)
            return
        _loadingSettings = true
        for (const key in saved)
            if (store[key] !== saved[key])
                store[key] = saved[key]
        _loadingSettings = false
    }
    onStartWithWindowsChanged: _push("startWithWindows", startWithWindows)
    onCloseHidesToTrayChanged: _push("closeHidesToTray", closeHidesToTray)
    onHotkeyChanged: _push("hotkey", hotkey)
    onFocusMsChanged: { _push("focusMs", focusMs); _lengthsChanged() }
    onShortMsChanged: { _push("shortMs", shortMs); _lengthsChanged() }
    onLongMsChanged: { _push("longMs", longMs); _lengthsChanged() }
    onReminderAlertsChanged: _push("reminderAlerts", reminderAlerts)
    onNotificationStyleChanged: _push("notificationStyle", notificationStyle)
    onNotificationSoundChanged: _push("notificationSound", notificationSound)
    onBackdropBlurChanged: _push("backdropBlur", backdropBlur)
    onBackdropDimChanged: _push("backdropDim", backdropDim)
    // A size above the maximum clamps to the maximum.
    onOverlaySizeChanged: {
        if (overlaySize > overlaySizeMax) {
            overlaySize = overlaySizeMax
            return
        }
        _push("overlaySize", overlaySize)
    }
    onAutoBackupChanged: _push("autoBackup", autoBackup)
    onKeepBackupsChanged: _push("keepBackups", keepBackups)
    onLibraryPathChanged: _push("libraryPath", libraryPath)
    onUserNameChanged: _push("userName", userName)
    onTourDoneChanged: _push("tourDone", tourDone)
    onOverlayLayoutJsonChanged: _push("overlayLayoutJson", overlayLayoutJson)
    onFilesViewModeChanged: _push("filesViewMode", filesViewMode)

    // ── The overlay's clock ──────────────────────────────────────────
    // `clockMs` is refreshed on the minute boundary (the timer re-aligns itself after every tick).
    // `clockOverride` (a test) pins "now" for the greeting and the clock. The shell sets
    // `twelveHour` from the Windows clock setting.
    property bool twelveHour: false
    property real clockOverride: 0
    property real clockMs: Date.now()
    readonly property real clockNow: clockOverride > 0 ? clockOverride : clockMs
    readonly property string greetingText: Dates.greeting(new Date(clockNow).getHours(), userName)
    readonly property var clockParts: Dates.clockParts(clockNow, twelveHour)
    readonly property int clockInterval: clockTimer.interval
    function refreshClock() { clockMs = Date.now() }
    // A tick: take the time, and aim the next tick at the coming minute boundary (+30 ms)
    function tickClock() {
        refreshClock()
        clockTimer.interval = 60000 - Date.now() % 60000 + 30
    }
    property Timer clockTimer: Timer {
        interval: 60000 - Date.now() % 60000 + 30
        repeat: true
        running: true
        onTriggered: store.tickClock()
    }

    // ── Data (from the Backend) ──────────────────────────────────────
    // { id, title, body, tint, pinned, archived, createdAt, updatedAt, deletedAt: null }, newest edit first
    property var notes: []
    // { id, title, description, due: ms | null, hasTime, priority: low|normal|high|urgent, done, doneAt, createdAt, deletedAt: null }
    property var tasks: []
    // { id, title, message, due: ms, done, doneAt, snoozeCount, createdAt, link: null | { kind: "task"|"note", id },
    //   repeat: null | rule (see recurrence.js), anchor: ms (the scheduled time a rule keeps the time of day of;
    //   a snooze moves `due` but not the anchor), timesDone: how often Done advanced a repeating reminder }
    property var reminders: []
    // { id, name, remaining: s, total: s, running, deadline: ms | null }: "running" = counting down
    // (false = paused, ready or finished)
    property var timers: []
    property var timerPresets: []          // [{ id, label, ms }]
    property var backups: []               // [{ id, at: ms, sizeMb, kind }]

    // The big Pomodoro timer is shared by the Full App rail, the Focus page and the overlay.
    property int focusTotal: 25 * 60
    property int focusRemaining: 25 * 60
    property bool focusRunning: false
    property int focusPhase: 0   // 0..7: focus, break, focus, break, focus, break, focus, long break

    readonly property string focusLabel: focusPhase % 2 === 0 ? "Session " + (focusPhase / 2 + 1) + " of 4"
                                       : focusPhase === 7 ? "Long break" : "Short break"

    // ── Derived views (memoized) ─────────────────────────────────────
    property var taskGroups: ({ overdue: [], today: [], tomorrow: [], week: [], later: [], none: [] })
    property var todayTasks: []                     // open overdue + today, then the ones done today
    property int todayDone: 0
    property int todayTotal: 0
    property var reminderGroups: ({ dueNow: [], today: [], tomorrow: [], week: [], later: [], past: [] })
    property var upcomingReminders: []              // not done, not due yet, by time
    property var trashItems: []                     // [{ kind, id, title, deletedAt, daysLeft }]
    property var focusStats: ({ sessionsToday: 0, minutesToday: 0, weekMinutes: 0, streak: 0 })
    property var _memo: ({})
    property var _trashRaw: []
    property var _timerBase: []
    property var _focusState: null

    function _assign(name, value) {
        const text = JSON.stringify(value)
        if (_memo[name] === text)
            return
        _memo[name] = text
        store[name] = value
    }
    function _rank(priority) { return ({ urgent: 3, high: 2, normal: 1, low: 0 })[priority] }
    function _compareTasks(a, b) {
        const da = a.due === null ? 0 : Dates.startOfDay(a.due), db = b.due === null ? 0 : Dates.startOfDay(b.due)
        if (da !== db) return da - db
        if (a.hasTime !== b.hasTime) return a.hasTime ? -1 : 1
        if (a.hasTime && a.due !== b.due) return a.due - b.due
        if (a.priority !== b.priority) return _rank(b.priority) - _rank(a.priority)
        return a.createdAt !== b.createdAt ? a.createdAt - b.createdAt : (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
    }
    function _refreshTasks() {
        const groups = { overdue: [], today: [], tomorrow: [], week: [], later: [], none: [] }
        const live = tasks.filter(t => !t.deletedAt && (!t.done || Dates.isToday(t.doneAt, now)))
        for (const t of live)
            groups[Dates.groupOf(t.due, now)].push(t)
        for (const key of Dates.GROUPS) {
            const open = groups[key].filter(t => !t.done).sort(_compareTasks)
            const done = groups[key].filter(t => t.done).sort((a, b) => a.doneAt - b.doneAt)
            groups[key] = open.concat(done)
        }
        _assign("taskGroups", groups)
        const today = groups.overdue.concat(groups.today)
        _assign("todayTasks", today.filter(t => !t.done).concat(today.filter(t => t.done)))
        _assign("todayDone", today.filter(t => t.done).length)
        _assign("todayTotal", today.length)
    }
    function _refreshReminders() {
        const groups = { dueNow: [], today: [], tomorrow: [], week: [], later: [], past: [] }
        for (const r of reminders) {
            if (r.done) groups.past.push(r)
            else if (r.due <= now) groups.dueNow.push(r)
            else groups[Dates.groupOf(r.due, now) === "overdue" ? "today" : Dates.groupOf(r.due, now)].push(r)
        }
        for (const key of ["dueNow", "today", "tomorrow", "week", "later"])
            groups[key].sort((a, b) => a.due - b.due)
        groups.past.sort((a, b) => b.doneAt - a.doneAt)
        _assign("reminderGroups", groups)
        _assign("upcomingReminders", groups.today.concat(groups.tomorrow, groups.week, groups.later))
    }
    function _refreshTrash() {
        const out = _trashRaw.map(item => Object.assign({}, item,
            { daysLeft: Math.max(0, Math.ceil((item.deletedAt + trashDays * 86400000 - now) / 86400000)) }))
        _assign("trashItems", out)
    }
    onTasksChanged: _refreshTasks()
    onRemindersChanged: _refreshReminders()
    onMinuteChanged: { _refreshTasks(); _refreshTrash() }

    readonly property int trashDays: 30

    // ── Loading from the Backend ─────────────────────────────────────
    function _projectTimers() {
        const at = Date.now()
        timers = _timerBase.map(t => t.running && t.deadline
            ? Object.assign({}, t, { remaining: Math.max(0, Math.ceil((t.deadline - at) / 1000)), running: t.deadline > at })
            : t)
    }
    function _applyFocus(s) {
        if (!s)
            return
        _focusState = s
        focusPhase = s.phase
        focusTotal = s.total
        _focusLoading = true
        focusRunning = s.running
        _focusLoading = false
        focusRemaining = s.running && s.deadline ? Math.max(0, Math.ceil((s.deadline - Date.now()) / 1000)) : s.remaining
    }
    property Connections backendLink: Connections {
        target: Backend
        function onSettingsChanged() { store._loadSettings() }
        function onNotesChanged() { store.notes = JSON.parse(Backend.notesJson) }
        function onTasksChanged() { store.tasks = JSON.parse(Backend.tasksJson) }
        function onRemindersChanged() { store.reminders = JSON.parse(Backend.remindersJson) }
        function onTimersChanged() { store._timerBase = JSON.parse(Backend.timersJson); store._projectTimers() }
        function onPresetsChanged() { store.timerPresets = JSON.parse(Backend.presetsJson) }
        function onTrashChanged() { store._trashRaw = JSON.parse(Backend.trashJson); store._refreshTrash() }
        function onBackupsChanged() { store.backups = JSON.parse(Backend.backupsJson) }
        function onFocusChanged() { store._applyFocus(JSON.parse(Backend.focusJson)) }
        function onStatsChanged() { store.focusStats = JSON.parse(Backend.statsJson) }
    }
    function _loadAll() {
        _loadSettings()
        notes = JSON.parse(Backend.notesJson)
        tasks = JSON.parse(Backend.tasksJson)
        reminders = JSON.parse(Backend.remindersJson)
        _timerBase = JSON.parse(Backend.timersJson)
        _projectTimers()
        timerPresets = JSON.parse(Backend.presetsJson)
        _trashRaw = JSON.parse(Backend.trashJson)
        _refreshTrash()
        backups = JSON.parse(Backend.backupsJson)
        _applyFocus(JSON.parse(Backend.focusJson))
        focusStats = JSON.parse(Backend.statsJson)
        _refreshTasks()
        _refreshReminders()
    }
    Component.onCompleted: _loadAll()

    // ── Pomodoro ─────────────────────────────────────────────────────
    property bool _focusLoading: false
    readonly property bool _focusActive: _focusState !== null && _focusState.active
    function focusDuration(phase) {
        if (phase % 2 === 0)
            return Math.round(focusMs / 1000)
        if (phase === 7)
            return Math.round(longMs / 1000)
        return Math.round(shortMs / 1000)
    }
    // Play / pause: the Pages assign this property.
    onFocusRunningChanged: if (!_focusLoading) Backend.focusSetRunning(focusRunning)
    function focusSkip() { Backend.focusSkip() }
    // Reset: pause and put the current session back to its full length.
    function focusReset() { Backend.focusReset() }
    // Start over: pause and go back to the first session at its full length.
    function focusStartOver() { Backend.focusStartOver() }
    // The length steppers (Focus page and Settings) change focusMs / shortMs / longMs. Before a
    // cycle has started the phase on screen follows them at once (a running cycle keeps the
    // lengths it started with).
    function _lengthsChanged() {
        if (_focusActive)
            return
        const total = focusDuration(focusPhase)
        if (total === focusTotal)
            return
        focusTotal = total
        if (!focusRunning)
            focusRemaining = total
    }

    // One 1 s ticker for the countdown numbers and the clock; the Backend decides what has ended.
    property Timer ticker: Timer {
        interval: 1000
        repeat: true
        running: true
        onTriggered: store._tick()
    }
    function _tick() {
        const at = Date.now()
        if (focusRunning && _focusState && _focusState.deadline)
            focusRemaining = Math.max(0, Math.ceil((_focusState.deadline - at) / 1000))
        if (_timerBase.some(t => t.running && t.deadline))
            _projectTimers()
        now = at
        const m = Math.floor(now / 60000) * 60000
        if (m !== minute)
            minute = m
        _refreshReminders()
    }

    // ── Notes ───────────────────────────────────────────────────────
    function addNote(title, body, tint) { return Backend.addNote(title, body, tint) }
    function updateNote(id, fields) { Backend.updateNote(id, fields) }
    // Deleting moves the note to the Trash.
    function deleteNote(id) { Backend.deleteNote(id) }
    function note(id) { return notes.find(n => n.id === id) }

    // ── Tasks ───────────────────────────────────────────────────────
    function task(id) { return tasks.find(t => t.id === id) }
    function toggleTask(id) { Backend.toggleTask(id) }
    function addTask(title, fields) { return Backend.addTask(title, fields || {}) || -1 }
    // Quick add: the text goes through the smart parser. `defaultToday`: a task without a date is
    // due today (the overlay's To-Do list and the rail's Today card show today's tasks).
    function addTaskFromText(text, defaultToday) {
        const p = Smart.parse(text, Date.now())
        if (p.title === "")
            return -1
        let due = p.due, hasTime = p.hasTime
        if (due === null && defaultToday) {
            due = Dates.dayAt(0, 0, 0)
            hasTime = false
        }
        return addTask(p.title, { due: due, hasTime: hasTime, priority: p.priority || "normal" })
    }
    function updateTask(id, fields) { Backend.updateTask(id, fields) }
    // Deleting moves the task to the Trash.
    function deleteTask(id) { Backend.deleteTask(id) }
    // Drag-rescheduling: move a task to the day range of a group (see Dates.moveToGroup).
    function moveTaskToGroup(id, group) {
        const t = task(id)
        if (!t)
            return false
        const moved = Dates.moveToGroup(t.due, t.hasTime, group, Date.now())
        if (moved === null)
            return false
        updateTask(id, { due: moved.due, hasTime: moved.hasTime })
        return true
    }

    // ── Reminders ───────────────────────────────────────────────────
    function reminder(id) { return reminders.find(r => r.id === id) }
    function addReminder(title, due, fields) { return Backend.addReminder(title, due, fields || {}) || -1 }
    function updateReminder(id, fields) { Backend.updateReminder(id, fields) }
    function snoozeReminder(id) { Backend.snoozeReminder(id) }
    // A new time for a reminder: it is open again and alerts again.
    function setReminderTime(id, ms) { Backend.setReminderTime(id, ms) }
    // Picking a repeat rule (null: Never) in the pop-out. The due time moves to the next valid
    // occurrence at or after the current one; a finished reminder is open again.
    function setReminderRepeat(id, rule) { Backend.setReminderRepeat(id, Recur.isValid(rule) ? rule : ({})) }   // {}: never
    // What a reminder's link points to: { kind, title, gone } (gone: deleted or in the Trash)
    function linkInfo(link) {
        if (!link)
            return null
        const trashed = trashItems.find(i => i.kind === link.kind && i.id === link.id)
        if (link.kind === "task") {
            const t = task(link.id)
            return { kind: "task", title: t ? t.title : (trashed ? trashed.title : "Deleted task"), gone: !t }
        }
        const n = note(link.id)
        const title = n ? (n.title !== "" ? n.title : (n.body.split("\n")[0] || "Empty note"))
                        : (trashed ? trashed.title : "Deleted note")
        return { kind: "note", title: title, gone: !n }
    }
    // Done: a one-time reminder moves to Past. A repeating one jumps to its next occurrence (strictly
    // in the future, past any it missed), forgets its snoozes and records the completion.
    function completeReminder(id) { Backend.completeReminder(id) }
    function deleteReminder(id) { Backend.deleteReminder(id) }
    // A reminder linked to a task, at the task's due time (a date-only task: 09:00 that day).
    // Returns the new reminder's id, or -1 when the task has no due date.
    function remindForTask(taskId) {
        const t = task(taskId)
        if (!t || t.due === null)
            return -1
        const due = t.hasTime ? t.due : Dates.withClock(t.due, 9, 0)
        return addReminder(t.title, due, { link: { kind: "task", id: taskId } })
    }

    // ── Timers ──────────────────────────────────────────────────────
    function toggleTimer(id) { Backend.toggleTimer(id) }
    function addTimer(name, seconds, start) { return Backend.addTimer(name, seconds, start !== false) || -1 }
    function startPreset(presetId) { return Backend.startPreset(presetId) || -1 }
    function resetTimer(id) { Backend.resetTimer(id) }
    function deleteTimer(id) { Backend.deleteTimer(id) }

    // ── Trash ───────────────────────────────────────────────────────
    function restoreItem(kind, id) { Backend.restoreItem(kind, id) }
    function deleteForever(kind, id) { Backend.deleteForever(kind, id) }
    function emptyTrash() { Backend.emptyTrash() }
    function purgeTrash() { Backend.purgeTrash() }

    // ── Backups ─────────────────────────────────────────────────────
    function backUpNow() { Backend.backUpNow() }
    function deleteBackup(id) { Backend.deleteBackup(id) }
    function restoreBackup(id) { Backend.restoreBackup(id) }
}
