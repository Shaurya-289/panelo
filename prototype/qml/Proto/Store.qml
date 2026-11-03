pragma Singleton
import QtQuick
import QtCore
import "dates.js" as Dates
import "smartparse.js" as Smart
import "recurrence.js" as Recur

// Fake in-memory data for the visual prototype, with real dates. Arrays are replaced (not
// mutated) so bindings that read them re-evaluate. The views read the derived, memoized
// properties (taskGroups, reminderGroups, ...): they only change when their content does, so a
// list that is being dragged is never rebuilt by the clock.
QtObject {
    id: store

    property int nextId: 100

    // ── Clock ────────────────────────────────────────────────────────
    property real now: Date.now()                                   // updated every second
    property real minute: Math.floor(Date.now() / 60000) * 60000    // updated once a minute (relative texts)

    // ── Settings (persisted; main.py points SettingsLocation at a throwaway file under test) ──
    property bool startWithWindows: false          // stored only: the prototype never touches the registry
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
    // A stored size above the maximum (an older setting) loads as the maximum.
    onOverlaySizeChanged: if (overlaySize > overlaySizeMax) overlaySize = overlaySizeMax
    property bool autoBackup: true
    property int keepBackups: 7
    property string libraryPath: ""
    property string userName: ""                   // asked by the first-launch tour; the overlay greets with it
    property bool tourDone: false                  // the first-launch tour has been finished or skipped

    property Settings prefs: Settings {
        category: "app"
        location: SettingsLocation
        property alias startWithWindows: store.startWithWindows
        property alias closeHidesToTray: store.closeHidesToTray
        property alias hotkey: store.hotkey
        property alias focusMs: store.focusMs
        property alias shortMs: store.shortMs
        property alias longMs: store.longMs
        property alias reminderAlerts: store.reminderAlerts
        property alias notificationStyle: store.notificationStyle
        property alias notificationSound: store.notificationSound
        property alias backdropBlur: store.backdropBlur
        property alias backdropDim: store.backdropDim
        property alias overlaySize: store.overlaySize
        property alias autoBackup: store.autoBackup
        property alias keepBackups: store.keepBackups
        property alias libraryPath: store.libraryPath
        property alias userName: store.userName
        property alias tourDone: store.tourDone
    }

    // ── The overlay's clock ──────────────────────────────────────────
    // `clockMs` is refreshed on the minute boundary (the timer re-aligns itself after every tick).
    // `clockOverride` (a test) pins "now" for the greeting and the clock. main.py sets `twelveHour`
    // from the Windows clock setting.
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

    // ── Data ─────────────────────────────────────────────────────────
    property var notes: [
        { id: 1, title: "Meeting notes", body: "- Budget review for Q3\n- Hiring: two designers\n- Ship the new glass UI before December\n- Follow up with Priya on the API", tint: "slate", pinned: true, archived: false, edited: "2h ago", deletedAt: null },
        { id: 2, title: "Ideas", body: "A dashboard that feels like Keep but darker, with acrylic glass and a quiet right rail.", tint: "plum", pinned: true, archived: false, edited: "Yesterday", deletedAt: null },
        { id: 3, title: "Recipes to try", body: "Pasta alla vodka\nThai green curry\nShakshuka", tint: "amber", pinned: false, archived: false, edited: "Mon", deletedAt: null },
        { id: 4, title: "Books to read", body: "- Dune Messiah\n- The Pragmatic Programmer\n- Deep Work\n- Piranesi", tint: "none", pinned: false, archived: false, edited: "Oct 28", deletedAt: null },
        { id: 5, title: "", body: "Call the landlord about the heating before Friday.", tint: "rose", pinned: false, archived: false, edited: "Oct 27", deletedAt: null },
        { id: 6, title: "Journal", body: "Today I finally cleaned up the project folder, wrote a README and got every test passing. Next up: a UI I actually like looking at.", tint: "none", pinned: false, archived: false, edited: "Oct 26", deletedAt: null },
        { id: 7, title: "Gift ideas", body: "Mom: ceramic planter\nArjun: mechanical keyboard", tint: "teal", pinned: false, archived: false, edited: "Oct 22", deletedAt: null },
        { id: 8, title: "Workout", body: "Mon / Wed / Fri\nSquat 5x5, bench 5x5, row 5x5\nTue / Thu: 30 min run", tint: "moss", pinned: false, archived: false, edited: "Oct 20", deletedAt: null },
        { id: 9, title: "Wi-Fi", body: "Network: home-5G\nPassword on the fridge", tint: "sand", pinned: false, archived: false, edited: "Oct 18", deletedAt: null },
        { id: 10, title: "Quotes", body: "\"Simplicity is the ultimate sophistication.\"", tint: "none", pinned: false, archived: false, edited: "Oct 15", deletedAt: null },
        { id: 11, title: "Old trip plan", body: "Goa, December. Flights booked.", tint: "teal", pinned: false, archived: true, edited: "Sep 30", deletedAt: null },
        { id: 12, title: "Kinematics formulas", body: "v = u + at\ns = ut + 1/2 at^2\nv^2 = u^2 + 2as", tint: "slate", pinned: false, archived: false, edited: "Oct 14", deletedAt: null },
        { id: 13, title: "Integration cheatsheet", body: "By parts, substitution, partial fractions. Revise trig identities first.", tint: "none", pinned: false, archived: false, edited: "Oct 12", deletedAt: null },
        { id: 14, title: "Periodic trends", body: "Electronegativity rises across a period and falls down a group.", tint: "moss", pinned: false, archived: false, edited: "Oct 9", deletedAt: null }
    ]

    // { id, title, description, due: ms | null, hasTime, priority: low|normal|high|urgent, done, doneAt, deletedAt }
    property var tasks: []
    // { id, title, message, due: ms, done, doneAt, snoozeCount, createdAt, link: null | { kind: "task"|"note", id },
    //   repeat: null | rule (see recurrence.js), anchor: ms (the scheduled time a rule keeps the time of day of;
    //   a snooze moves `due` but not the anchor), history: [ms] (completions of a repeating reminder) }
    property var reminders: []
    // "running" = counting down (false = paused). Every timer here is an active one.
    property var timers: []
    readonly property var timerPresets: [
        { id: 1, label: "5m", ms: 5 * 60000 },
        { id: 2, label: "10m", ms: 10 * 60000 },
        { id: 3, label: "15m", ms: 15 * 60000 },
        { id: 4, label: "1h", ms: 60 * 60000 }
    ]
    property var focusHistory: []          // [{ start: ms, minutes }]
    property var backups: []               // [{ id, at: ms, sizeMb }]

    // The big Pomodoro timer is shared by the Full App rail, the Focus page and the overlay.
    property int focusTotal: 25 * 60
    property int focusRemaining: 24 * 60 + 13
    property bool focusRunning: false
    property int focusPhase: 2   // 0..7: focus, break, focus, break, focus, break, focus, long break
    property bool fastFocus: false   // --fast-focus: 20 s focus / 8 s break / 12 s long break

    readonly property string focusLabel: focusPhase % 2 === 0 ? "Session " + (focusPhase / 2 + 1) + " of 4"
                                       : focusPhase === 7 ? "Long break" : "Short break"

    // main.py shows these as toasts
    signal focusCompleted(string message)
    signal reminderFired(var reminder)
    signal timerFinished(string name)
    signal backupRestored(string text)

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
    property var _notified: ({})                    // reminder ids that already fired their alert

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
        return a.id - b.id
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
        _refreshTrash()
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
        const out = []
        for (const n of notes)
            if (n.deletedAt)
                out.push({ kind: "note", id: n.id, title: n.title !== "" ? n.title : (n.body.split("\n")[0] || "Empty note"), deletedAt: n.deletedAt })
        for (const t of tasks)
            if (t.deletedAt)
                out.push({ kind: "task", id: t.id, title: t.title, deletedAt: t.deletedAt })
        out.sort((a, b) => b.deletedAt - a.deletedAt)
        for (const item of out)
            item.daysLeft = Math.max(0, Math.ceil((item.deletedAt + trashDays * 86400000 - now) / 86400000))
        _assign("trashItems", out)
    }
    function _refreshStats() {
        const days = {}
        let sessionsToday = 0, minutesToday = 0, week = 0
        for (const s of focusHistory) {
            const ago = -Dates.dayDiff(s.start, now)
            days[ago] = true
            if (ago === 0) { sessionsToday += 1; minutesToday += s.minutes }
            if (ago >= 0 && ago <= 6) week += s.minutes
        }
        let streak = 0, day = days[0] ? 0 : 1
        while (days[day]) { streak += 1; day += 1 }
        _assign("focusStats", { sessionsToday: sessionsToday, minutesToday: minutesToday, weekMinutes: week, streak: streak })
    }
    onTasksChanged: _refreshTasks()
    onNotesChanged: _refreshTrash()
    onRemindersChanged: _refreshReminders()
    onFocusHistoryChanged: _refreshStats()
    onMinuteChanged: { _refreshTasks(); _refreshStats() }

    readonly property int trashDays: 30

    // ── Pomodoro ─────────────────────────────────────────────────────
    function focusDuration(phase) {
        if (phase % 2 === 0)
            return fastFocus ? 20 : Math.round(focusMs / 1000)
        if (phase === 7)
            return fastFocus ? 12 : Math.round(longMs / 1000)
        return fastFocus ? 8 : Math.round(shortMs / 1000)
    }
    function focusSetPhase(phase) {
        focusPhase = phase
        focusTotal = focusDuration(phase)
        focusRemaining = focusTotal
    }
    function focusSkip() { focusSetPhase((focusPhase + 1) % 8) }
    // Reset: pause and put the current session back to its full length.
    function focusReset() {
        focusRunning = false
        focusRemaining = focusTotal
    }
    // Start over: pause and go back to the first session at its full length.
    function focusStartOver() {
        focusRunning = false
        focusSetPhase(0)
    }
    // The length steppers (Focus page and Settings) change focusMs / shortMs / longMs; the phase
    // on screen follows when its own length changed.
    function _lengthsChanged() {
        const total = focusDuration(focusPhase)
        if (total === focusTotal)
            return
        focusTotal = total
        focusRemaining = focusRunning ? Math.min(focusRemaining, total) : total
    }
    onFocusMsChanged: _lengthsChanged()
    onShortMsChanged: _lengthsChanged()
    onLongMsChanged: _lengthsChanged()

    function _focusFinished() {
        const done = focusPhase
        const next = (done + 1) % 8
        if (done % 2 === 0)
            focusHistory = focusHistory.concat([{ start: Date.now() - focusTotal * 1000, minutes: Math.max(1, Math.round(focusTotal / 60)) }])
        focusSetPhase(next)
        focusRunning = false
        let message
        if (done === 7)
            message = "Break over — ready for session 1?"
        else if (done === 6)
            message = "Cycle complete — take a 15 min break"
        else if (done % 2 === 0)
            message = "Focus session done — time for a 5 min break"
        else
            message = "Break over — ready for session " + (next / 2 + 1) + "?"
        focusCompleted(message)
    }

    // One 1 s ticker for every live countdown, and for reminders that come due.
    property Timer ticker: Timer {
        interval: 1000
        repeat: true
        running: true
        onTriggered: store._tick()
    }
    function _tick() {
        if (focusRunning) {
            focusRemaining = Math.max(0, focusRemaining - 1)
            if (focusRemaining === 0)
                _focusFinished()
        }
        if (timers.some(t => t.running && t.remaining > 0)) {
            const finished = []
            timers = timers.map(t => {
                if (!t.running || t.remaining <= 0)
                    return t
                const left = t.remaining - 1
                if (left === 0)
                    finished.push(t.name)
                return Object.assign({}, t, { remaining: left, running: left > 0 })
            })
            for (const name of finished)
                timerFinished(name)
        }
        now = Date.now()
        const m = Math.floor(now / 60000) * 60000
        if (m !== minute)
            minute = m
        _refreshReminders()
        for (const r of reminders.filter(r => !r.done && r.due <= now && !_notified[r.id])) {
            _notified[r.id] = true
            reminderFired(r)
        }
    }

    // Hourly: anything that has been in the Trash for 30 days is deleted for good.
    property Timer purger: Timer {
        interval: 3600000
        repeat: true
        running: true
        onTriggered: store.purgeTrash()
    }

    function _replace(list, id, fields) {
        return list.map(item => item.id === id ? Object.assign({}, item, fields) : item)
    }

    // ── Notes ───────────────────────────────────────────────────────
    function addNote(title, body, tint) {
        if (title.trim() === "" && body.trim() === "")
            return
        nextId += 1
        notes = [{ id: nextId, title: title, body: body, tint: tint, pinned: false, archived: false, edited: "Just now", deletedAt: null }].concat(notes)
    }
    function updateNote(id, fields) { notes = _replace(notes, id, Object.assign({ edited: "Just now" }, fields)) }
    // Deleting moves the note to the Trash.
    function deleteNote(id) { notes = _replace(notes, id, { deletedAt: Date.now() }) }
    function note(id) { return notes.find(n => n.id === id) }

    // ── Tasks ───────────────────────────────────────────────────────
    function task(id) { return tasks.find(t => t.id === id) }
    function toggleTask(id) {
        const t = task(id)
        if (!t)
            return
        tasks = _replace(tasks, id, { done: !t.done, doneAt: !t.done ? Date.now() : null })
    }
    function addTask(title, fields) {
        if (title.trim() === "")
            return -1
        nextId += 1
        tasks = tasks.concat([Object.assign({ id: nextId, title: title.trim(), description: "", due: null, hasTime: false,
                                              priority: "normal", done: false, doneAt: null, deletedAt: null }, fields || {})])
        return nextId
    }
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
    function updateTask(id, fields) { tasks = _replace(tasks, id, fields) }
    // Deleting moves the task to the Trash.
    function deleteTask(id) { tasks = _replace(tasks, id, { deletedAt: Date.now() }) }
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
    function addReminder(title, due, fields) {
        if (title.trim() === "")
            return -1
        nextId += 1
        if (due <= Date.now())
            _notified[nextId] = true     // already due when created: shown as due, no alert
        reminders = reminders.concat([Object.assign({ id: nextId, title: title.trim(), message: "", due: due, done: false,
                                                      doneAt: null, snoozeCount: 0, createdAt: Date.now(), link: null,
                                                      repeat: null, anchor: due, history: [] }, fields || {})])
        return nextId
    }
    function updateReminder(id, fields) { reminders = _replace(reminders, id, fields) }
    function snoozeReminder(id) {
        const r = reminder(id)
        if (!r)
            return
        delete _notified[id]
        updateReminder(id, { due: Date.now() + 10 * 60000, done: false, doneAt: null, snoozeCount: r.snoozeCount + 1 })
    }
    // A new time for a reminder: it is open again and alerts again.
    function setReminderTime(id, ms) {
        delete _notified[id]
        if (ms <= Date.now())
            _notified[id] = true
        updateReminder(id, { due: ms, anchor: ms, done: false, doneAt: null })
    }
    // Picking a repeat rule (null: Never) in the pop-out. The due time moves to the next valid
    // occurrence at or after the current one; a finished reminder is open again.
    function setReminderRepeat(id, rule) {
        const r = reminder(id)
        if (!r)
            return
        if (!Recur.isValid(rule)) {
            updateReminder(id, { repeat: null })
            return
        }
        const fields = { repeat: rule }
        let due = r.due
        if (r.done) {
            due = Math.max(r.due, Date.now())
            fields.done = false
            fields.doneAt = null
        }
        const next = Recur.alignDue(rule, due)
        delete _notified[id]
        if (next <= Date.now())
            _notified[id] = true
        fields.due = next
        fields.anchor = next
        updateReminder(id, fields)
    }
    // What a reminder's link points to: { kind, title, gone } (gone: deleted or in the Trash)
    function linkInfo(link) {
        if (!link)
            return null
        if (link.kind === "task") {
            const t = task(link.id)
            return { kind: "task", title: t ? t.title : "Deleted task", gone: !t || !!t.deletedAt }
        }
        const n = note(link.id)
        const title = n ? (n.title !== "" ? n.title : (n.body.split("\n")[0] || "Empty note")) : "Deleted note"
        return { kind: "note", title: title, gone: !n || !!n.deletedAt }
    }
    // Done: a one-time reminder moves to Past. A repeating one jumps to its next occurrence (strictly
    // in the future, past any it missed), forgets its snoozes and records the completion.
    function completeReminder(id) {
        const r = reminder(id)
        if (r && Recur.isValid(r.repeat)) {
            const nowMs = Date.now()
            const next = Recur.advance(r.repeat, r.due, r.anchor, nowMs)
            delete _notified[id]
            updateReminder(id, { due: next, anchor: next, snoozeCount: 0, history: (r.history || []).concat([nowMs]).slice(-50) })
            return
        }
        updateReminder(id, { done: true, doneAt: Date.now() })
    }
    function deleteReminder(id) { reminders = reminders.filter(r => r.id !== id) }
    // A reminder linked to a task, at the task's due time (a date-only task: 09:00 that day).
    // Returns the new reminder's id, or -1 when the task has no due date.
    function remindForTask(taskId) {
        const t = task(taskId)
        if (!t || t.due === null)
            return -1
        const due = t.hasTime ? t.due : Dates.withClock(t.due, 9, 0)
        return addReminder(t.title, due, { link: { kind: "task", id: taskId } })
    }

    // Demo: a reminder that comes due right now.
    property int _demoCount: 0
    function fireDemoReminder() {
        const titles = ["Stand up and stretch", "Take your vitamins", "Check the oven"]
        const id = addReminder(titles[_demoCount % titles.length], Date.now(), {})
        _demoCount += 1
        reminderFired(reminder(id))
    }

    // ── Timers ──────────────────────────────────────────────────────
    function toggleTimer(id) {
        const timer = timers.find(t => t.id === id)
        if (!timer)
            return
        const restart = timer.remaining <= 0
        timers = _replace(timers, id, { running: !timer.running, remaining: restart ? timer.total : timer.remaining })
    }
    function addTimer(name, seconds, start) {
        if (seconds <= 0)
            return -1
        nextId += 1
        timers = timers.concat([{ id: nextId, name: name.trim() !== "" ? name.trim() : "Timer", remaining: seconds, total: seconds, running: start !== false }])
        return nextId
    }
    function startPreset(presetId) {
        const preset = timerPresets.find(p => p.id === presetId)
        if (!preset)
            return -1
        const minutes = Math.round(preset.ms / 60000)
        return addTimer(minutes === 60 ? "1 h timer" : minutes + " min timer", Math.round(preset.ms / 1000), true)
    }
    function resetTimer(id) {
        const timer = timers.find(t => t.id === id)
        if (timer)
            timers = _replace(timers, id, { remaining: timer.total, running: false })
    }
    function deleteTimer(id) { timers = timers.filter(t => t.id !== id) }

    // ── Trash ───────────────────────────────────────────────────────
    function restoreItem(kind, id) {
        if (kind === "note") notes = _replace(notes, id, { deletedAt: null })
        else tasks = _replace(tasks, id, { deletedAt: null })
    }
    function deleteForever(kind, id) {
        if (kind === "note") notes = notes.filter(n => n.id !== id)
        else tasks = tasks.filter(t => t.id !== id)
    }
    function emptyTrash() {
        notes = notes.filter(n => !n.deletedAt)
        tasks = tasks.filter(t => !t.deletedAt)
    }
    function purgeTrash() {
        const limit = Date.now() - trashDays * 86400000
        if (notes.some(n => n.deletedAt && n.deletedAt < limit))
            notes = notes.filter(n => !n.deletedAt || n.deletedAt >= limit)
        if (tasks.some(t => t.deletedAt && t.deletedAt < limit))
            tasks = tasks.filter(t => !t.deletedAt || t.deletedAt >= limit)
    }

    // ── Backups (fake) ──────────────────────────────────────────────
    function backUpNow() {
        nextId += 1
        const keep = Math.max(1, keepBackups)
        backups = [{ id: nextId, at: Date.now(), sizeMb: 4.6 }].concat(backups).slice(0, keep)
    }
    function deleteBackup(id) { backups = backups.filter(b => b.id !== id) }
    function restoreBackup(id) {
        const b = backups.find(b => b.id === id)
        if (b)
            backupRestored("Backup from " + Dates.longDate(b.at) + " restored (prototype: nothing changed)")
    }

    // ── Seed data ───────────────────────────────────────────────────
    function _seed() {
        now = Date.now()
        const D = Dates.dayAt
        const at = (offset, h, m) => D(offset, h, m)
        const day = (offset) => D(offset, 0, 0)
        const t = (id, title, due, hasTime, priority, extra) => Object.assign({ id: id, title: title, description: "", due: due,
            hasTime: hasTime, priority: priority, done: false, doneAt: null, deletedAt: null }, extra || {})
        const hoursAgo = (h) => Date.now() - h * 3600000
        tasks = [
            t(1, "Submit expense report", day(-3), false, "high"),
            t(2, "Renew library card", at(-1, 17, 0), true, "normal"),
            t(3, "Finish the quarterly report", at(0, 17, 0), true, "urgent", { description: "Numbers from finance are in the shared sheet. Add the Q3 summary slide." }),
            t(4, "Call the dentist", at(0, 11, 30), true, "normal"),
            t(5, "Review pull request", day(0), false, "high"),
            t(6, "Buy groceries", day(0), false, "normal", { done: true, doneAt: hoursAgo(3) }),
            t(7, "Water the plants", day(0), false, "low", { done: true, doneAt: hoursAgo(2) }),
            t(8, "Prepare slides for standup", at(1, 9, 0), true, "normal"),
            t(9, "Gym session", at(3, 18, 0), true, "low"),
            t(10, "Read chapter 4", day(5), false, "normal"),
            t(11, "Book flights for Goa", day(14), false, "high"),
            t(12, "Renew passport", day(30), false, "low"),
            t(13, "Learn Rust basics", null, false, "normal"),
            t(14, "Sort old photos", null, false, "low"),
            t(15, "Reply to Sam", day(-2), false, "normal", { done: true, doneAt: Date.now() - 30 * 3600000 }),
            t(16, "Pay electricity bill", day(-4), false, "urgent", { done: true, doneAt: Date.now() - 52 * 3600000 }),
            t(20, "Cancel gym trial", day(-6), false, "normal", { deletedAt: Date.now() - 5 * 86400000 - 7200000 }),
            t(21, "Return headphones", day(-9), false, "low", { deletedAt: Date.now() - 27 * 86400000 - 7200000 }),
            t(22, "Ancient chore (purged on load)", day(-60), false, "low", { deletedAt: Date.now() - 45 * 86400000 })
        ]
        notes = notes.concat([
            { id: 15, title: "Old shopping list", body: "Milk, eggs, coffee, batteries", tint: "sand", pinned: false, archived: false, edited: "Oct 3", deletedAt: Date.now() - 2 * 86400000 - 7200000 },
            { id: 16, title: "Draft email to Priya", body: "Hi Priya, following up on the API review...", tint: "none", pinned: false, archived: false, edited: "Sep 20", deletedAt: Date.now() - 20 * 86400000 - 7200000 }
        ])
        const r = (id, title, due, extra) => Object.assign({ id: id, title: title, message: "", due: due, done: false, doneAt: null,
            snoozeCount: 0, createdAt: Date.now() - 3 * 86400000, link: null, repeat: null, anchor: due, history: [] }, extra || {})
        const gymRule = { kind: "weekly", days: [1, 3, 5] }, rentRule = { kind: "monthly", day: 1 }
        const waterDue = Math.floor((Date.now() + 95 * 60000) / 60000) * 60000
        const gymDue = Recur.nextOccurrence(gymRule, Date.now(), at(0, 18, 0))
        const rentDue = Recur.nextOccurrence(rentRule, Date.now(), at(0, 9, 0))
        reminders = [
            r(1, "Drink water", Date.now() - 60000, { message: "Keep a glass on the desk." }),
            r(2, "Gym", Date.now() + 52 * 60000, { message: "Leg day. Bring the blue towel." }),
            r(3, "Prepare for standup", at(1, 9, 30), { link: { kind: "task", id: 8 }, snoozeCount: 1 }),
            r(4, "Review the meeting notes", at(3, 10, 0), { link: { kind: "note", id: 1 } }),
            r(5, "Renew the domain", at(12, 9, 0), { message: "Auto-renew is off." }),
            r(6, "Take vitamins", at(-1, 8, 0), { done: true, doneAt: at(-1, 8, 2) }),
            r(7, "Call Priya", at(-2, 15, 0), { done: true, doneAt: at(-2, 15, 40), snoozeCount: 2 }),
            r(8, "Pay rent", at(-5, 9, 0), { done: true, doneAt: at(-5, 9, 1) }),
            r(9, "Dentist check-in", at(-8, 13, 0), { done: true, doneAt: at(-8, 13, 5) }),
            r(10, "Drink water", waterDue, { repeat: { kind: "every", n: 2, unit: "hours" },
                history: [1, 2, 3, 4, 5].map(h => Date.now() - h * 7200000) }),
            r(11, "Gym", gymDue, { repeat: gymRule, history: [at(-2, 18, 1), at(-4, 18, 3), at(-7, 18, 0)] }),
            r(12, "Pay rent", rentDue, { repeat: rentRule, history: [1, 2, 3, 4, 5].map(m => D(-30 * m, 9, 2)) })
        ]
        _notified = { 1: true }
        timers = [
            { id: 1, name: "Tea", remaining: 180, total: 300, running: true },
            { id: 2, name: "Laundry", remaining: 1520, total: 2700, running: true },
            { id: 3, name: "Stretch break", remaining: 600, total: 600, running: false }
        ]
        const sessions = []
        const perDay = { 0: 3, 1: 4, 2: 2, 3: 3, 5: 4, 6: 2, 8: 3, 9: 1 }
        for (const ago of Object.keys(perDay))
            for (let i = 0; i < perDay[ago]; i++)
                sessions.push({ start: D(-Number(ago), 9 + i, 0), minutes: 25 })
        focusHistory = sessions
        const list = []
        for (let i = 0; i < 5; i++)
            list.push({ id: 200 + i, at: D(-i, 3, 0), sizeMb: 4.2 + i * 0.1 })
        backups = list
        if (focusDuration(focusPhase) !== focusTotal) {
            focusTotal = focusDuration(focusPhase)
            focusRemaining = Math.min(focusRemaining, focusTotal)
        }
        purgeTrash()
        _refreshTasks()
        _refreshReminders()
        _refreshStats()
    }
    Component.onCompleted: _seed()
}
