.pragma library

// Date helpers shared by the Store, the pages and the self-test. Every function that depends on
// "now" takes it as an optional last argument (milliseconds since the epoch), so the tests can pin it.

var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
var DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
var MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"]
var GROUPS = ["overdue", "today", "tomorrow", "week", "later", "none"]
var GROUP_LABELS = { overdue: "Overdue", today: "Today", tomorrow: "Tomorrow", week: "This week",
                     later: "Later", none: "No date" }

function two(n) { return (n < 10 ? "0" : "") + n }
function clock(ms) { var d = new Date(ms); return two(d.getHours()) + ":" + two(d.getMinutes()) }
function nowOf(now) { return now === undefined || now === null ? Date.now() : now }

function startOfDay(ms) {
    var d = new Date(ms)
    d.setHours(0, 0, 0, 0)
    return d.getTime()
}

// ms moved by whole calendar days (keeps the time of day, also across daylight-saving changes)
function addDays(ms, n) {
    var d = new Date(ms)
    d.setDate(d.getDate() + n)
    return d.getTime()
}

// Calendar days from today to the day of `ms` (negative: in the past)
function dayDiff(ms, now) {
    return Math.round((startOfDay(ms) - startOfDay(nowOf(now))) / 86400000)
}

// The day `offset` days from today at hh:mm (hasTime false: at 00:00)
function dayAt(offset, hours, minutes, now) {
    var d = new Date(addDays(startOfDay(nowOf(now)), offset))
    d.setHours(hours || 0, minutes || 0, 0, 0)
    return d.getTime()
}

// The same day as `ms` at hh:mm
function withClock(ms, hours, minutes) {
    var d = new Date(ms)
    d.setHours(hours, minutes, 0, 0)
    return d.getTime()
}

// The next Monday after today
function nextMonday(now) {
    var today = startOfDay(nowOf(now))
    var weekday = new Date(today).getDay()
    return addDays(today, ((8 - weekday) % 7) || 7)
}

// overdue / today / tomorrow / week / later / none. A task is overdue once its day has passed
// (a task due today stays in "today" for the whole day, whatever its time).
function groupOf(ms, now) {
    if (ms === null || ms === undefined)
        return "none"
    var d = dayDiff(ms, now)
    if (d < 0) return "overdue"
    if (d === 0) return "today"
    if (d === 1) return "tomorrow"
    if (d <= 7) return "week"
    return "later"
}

function isToday(ms, now) {
    return ms !== null && ms !== undefined && dayDiff(ms, now) === 0
}

// "now", "in 52 min", "in 3 h", "2 h ago", "tomorrow", "in 4 days", "3 days ago"
function relative(ms, now) {
    var diff = ms - nowOf(now)
    var future = diff >= 0
    var mins = Math.floor(Math.abs(diff) / 60000)
    if (Math.abs(diff) < 60000)
        return "now"
    var span
    if (mins < 60) {
        span = mins + " min"
    } else if (mins < 24 * 60) {
        var h = Math.floor(mins / 60), m = mins % 60
        span = h + " h" + (m !== 0 && h < 6 ? " " + m + " min" : "")
    } else {
        var d = dayDiff(ms, now)
        if (d === 1) return "tomorrow"
        if (d === -1) return "yesterday"
        span = Math.abs(d) + " days"
    }
    return future ? "in " + span : span + " ago"
}

// Due text for a row: "17:00", "Mon", "Mon 09:30", "Nov 12". Today and tomorrow show only the
// time (their group header already names the day); without a time they show "Today" / "Tomorrow".
function formatDue(ms, hasTime, now) {
    if (ms === null || ms === undefined)
        return ""
    var d = dayDiff(ms, now)
    var time = hasTime ? clock(ms) : ""
    if (d === 0) return hasTime ? time : "Today"
    if (d === 1) return hasTime ? time : "Tomorrow"
    var day
    if (d === -1) day = "Yesterday"
    else if (d >= -6 && d <= 7) day = DAYS[new Date(ms).getDay()]
    else day = MONTHS[new Date(ms).getMonth()] + " " + new Date(ms).getDate()
    return hasTime ? day + " " + time : day
}

// Like formatDue, but the day is always named: "Today 17:00", "Tomorrow 09:00", "Mon 09:30"
function describe(ms, hasTime, now) {
    if (ms === null || ms === undefined)
        return "No date"
    var d = dayDiff(ms, now)
    if (d === 0 || d === 1)
        return (d === 0 ? "Today" : "Tomorrow") + (hasTime ? " " + clock(ms) : "")
    return formatDue(ms, hasTime, now)
}

// Time of a reminder: "18:00" today, "Tue 09:30" on another day
// (a weekday only within the coming week; further out it shows the date, like the Reminders page)
function formatWhen(ms, now) {
    return dayDiff(ms, now) === 0 ? clock(ms) : describe(ms, true, now)
}

function longDate(ms) {
    var d = new Date(ms)
    return DAYS[d.getDay()] + ", " + MONTHS[d.getMonth()] + " " + d.getDate()
}

// "Mon", "Oct 28": for "created Mon" style info
function shortDay(ms, now) {
    var d = dayDiff(ms, now)
    if (d === 0) return "today"
    if (d === -1) return "yesterday"
    if (d > -7 && d < 0) return DAYS[new Date(ms).getDay()]
    return MONTHS[new Date(ms).getMonth()] + " " + new Date(ms).getDate()
}

// The day (a task's due) moved to a group: Today / Tomorrow / This week (the first day of that
// range) / Later (+8 days) keep the time of day; "none" clears it. Returns { due, hasTime }, or
// null when a task can't be dropped there (Overdue).
function moveToGroup(due, hasTime, group, now) {
    if (group === "overdue")
        return null
    if (group === "none")
        return { due: null, hasTime: false }
    var offset = { today: 0, tomorrow: 1, week: 2, later: 8 }[group]
    if (offset === undefined)
        return null
    var base = dayAt(offset, 0, 0, now)
    if (due !== null && due !== undefined && hasTime) {
        var old = new Date(due)
        return { due: withClock(base, old.getHours(), old.getMinutes()), hasTime: true }
    }
    return { due: base, hasTime: false }
}

function sameDay(a, b) { return startOfDay(a) === startOfDay(b) }

// The 42 cells (six weeks, Monday first) of a month grid, as day-start timestamps. Days outside
// the month fill the first and last weeks.
function monthCells(year, month) {
    var lead = (new Date(year, month, 1).getDay() + 6) % 7
    var cells = []
    for (var i = 0; i < 42; i++)
        cells.push(new Date(year, month, 1 - lead + i).getTime())
    return cells
}

function humanMinutes(minutes) {
    var h = Math.floor(minutes / 60), m = minutes % 60
    if (h === 0) return m + "m"
    return h + "h" + (m !== 0 ? " " + two(m) + "m" : "")
}

// The overlay's greeting: Good morning 05:00-11:59, afternoon 12:00-16:59, evening 17:00-21:59 and
// night 22:00-04:59, with ", <name>" when a name is set
function greeting(hour, name) {
    var part = hour >= 5 && hour < 12 ? "morning" : hour >= 12 && hour < 17 ? "afternoon" : hour >= 17 && hour < 22 ? "evening" : "night"
    var who = name === null || name === undefined ? "" : String(name).trim()
    return "Good " + part + (who !== "" ? ", " + who : "")
}

// The overlay's clock: { text, suffix }. 24-hour: "14:05" with no suffix. 12-hour: "2:05" and "PM".
function clockParts(ms, twelveHour) {
    var d = new Date(ms)
    var h = d.getHours(), m = two(d.getMinutes())
    if (!twelveHour)
        return { text: two(h) + ":" + m, suffix: "" }
    return { text: ((h % 12) || 12) + ":" + m, suffix: h < 12 ? "AM" : "PM" }
}
