.pragma library

// Repeating reminders. A rule is one of
//   { kind: "daily" }
//   { kind: "weekdays" }                        // Mon-Fri
//   { kind: "weekly", days: [1, 3, 5] }         // 0 = Sun .. 6 = Sat, at least one day
//   { kind: "monthly", day: 1 }                 // 1..31; a short month uses its last day
//   { kind: "yearly" }                          // the month and day of the anchor
//   { kind: "every", n: 2, unit: "hours" | "days" | "weeks" }
// Every rule keeps the time of day of the anchor (the reminder's scheduled time). `every N hours`
// steps from the anchor itself.

var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
var HOUR = 3600000
var SEARCH_DAYS = 800      // the longest gap between two occurrences is a Feb 29 clamped to Feb 28: < 400

// A real array from a JS array or from the list a delegate's modelData turns it into (a QVariantList
// is not an Array: Array.isArray fails, but it has a length)
function toArray(list) {
    var out = []
    if (list === null || list === undefined || typeof list.length !== "number")
        return out
    for (var i = 0; i < list.length; i++)
        out.push(list[i])
    return out
}

function isValid(rule) {
    if (!rule || typeof rule.kind !== "string")
        return false
    switch (rule.kind) {
    case "daily": case "weekdays": case "yearly":
        return true
    case "weekly":
        var days = toArray(rule.days)
        return days.length > 0 && days.every(function (d) { return d >= 0 && d <= 6 && d === Math.floor(d) })
    case "monthly":
        return rule.day >= 1 && rule.day <= 31 && rule.day === Math.floor(rule.day)
    case "every":
        return rule.n >= 1 && rule.n === Math.floor(rule.n)
            && (rule.unit === "hours" || rule.unit === "days" || rule.unit === "weeks")
    }
    return false
}

function daysInMonth(year, month) { return new Date(year, month + 1, 0).getDate() }

// "1st", "2nd", "3rd", "4th", "11th", "21st"...
function ordinal(n) {
    var teen = n % 100
    if (teen >= 11 && teen <= 13)
        return n + "th"
    var suffix = { 1: "st", 2: "nd", 3: "rd" }[n % 10]
    return n + (suffix || "th")
}

// The day `offset` calendar days after the day of `ms`, at `anchor`'s time of day (DST-safe)
function dayWithTime(ms, offset, anchor) {
    var d = new Date(ms), t = new Date(anchor)
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() + offset,
                    t.getHours(), t.getMinutes(), t.getSeconds(), t.getMilliseconds()).getTime()
}

function matchesDay(rule, ms, anchor) {
    var d = new Date(ms), wd = d.getDay()
    switch (rule.kind) {
    case "daily":
        return true
    case "weekdays":
        return wd >= 1 && wd <= 5
    case "weekly":
        return toArray(rule.days).indexOf(wd) >= 0
    case "monthly":
        return d.getDate() === Math.min(rule.day, daysInMonth(d.getFullYear(), d.getMonth()))
    case "yearly":
        var a = new Date(anchor)
        return d.getMonth() === a.getMonth()
            && d.getDate() === Math.min(a.getDate(), daysInMonth(d.getFullYear(), a.getMonth()))
    }
    return false
}

// The next due time strictly after `afterMs`, or null for an invalid rule. `anchorMs` supplies
// the time of day (and, for yearly, the month and day); for `every` it is also the first step.
// Occurrences missed while the app was closed are skipped: the result is always after `afterMs`.
function nextOccurrence(rule, afterMs, anchorMs) {
    if (!isValid(rule))
        return null
    if (rule.kind === "every") {
        if (rule.unit === "hours") {
            var step = rule.n * HOUR
            var k = Math.floor((afterMs - anchorMs) / step) + 1
            var at = anchorMs + k * step
            while (at <= afterMs) at += step      // (guards against float rounding)
            while (at - step > afterMs) at -= step
            return at
        }
        var days = rule.n * (rule.unit === "weeks" ? 7 : 1)
        var first = dayWithTime(anchorMs, 0, anchorMs)
        var gone = Math.round((dayWithTime(afterMs, 0, afterMs) - first) / 86400000)
        var count = Math.max(0, Math.floor(gone / days) - 1)
        var cand = dayWithTime(anchorMs, count * days, anchorMs)
        while (cand <= afterMs) {
            count += 1
            cand = dayWithTime(anchorMs, count * days, anchorMs)
        }
        return cand
    }
    for (var i = 0; i < SEARCH_DAYS; i++) {
        var candidate = dayWithTime(afterMs, i, anchorMs)
        if (candidate > afterMs && matchesDay(rule, candidate, anchorMs))
            return candidate
    }
    return null
}

// The first valid due time at or after `dueMs` (what picking a rule does to the current time)
function alignDue(rule, dueMs) {
    return nextOccurrence(rule, dueMs - 1, dueMs)
}

// What Done does to a repeating reminder: the next occurrence, strictly in the future and after
// the current due time. `anchorMs` is the scheduled time (it differs from `dueMs` after a snooze).
function advance(rule, dueMs, anchorMs, nowMs) {
    var base = rule.kind === "every" && rule.unit === "hours" ? dueMs : (anchorMs || dueMs)
    return nextOccurrence(rule, Math.max(nowMs, dueMs), base)
}

function sameRule(a, b) { return JSON.stringify(a || null) === JSON.stringify(b || null) }

// Weekday numbers in Monday-first order, without duplicates
function sortedDays(days) {
    var seen = {}, out = []
    toArray(days).forEach(function (d) { seen[d] = true })
    ;[1, 2, 3, 4, 5, 6, 0].forEach(function (d) { if (seen[d]) out.push(d) })
    return out
}

// "every day", "weekdays", "every Mon, Wed, Fri", "monthly on the 1st", "every year on Nov 3",
// "every 2 hours". Yearly needs the anchor for its date (without it: "every year").
function describe(rule, anchorMs) {
    if (!isValid(rule))
        return ""
    switch (rule.kind) {
    case "daily": return "every day"
    case "weekdays": return "weekdays"
    case "weekly": return "every " + sortedDays(rule.days).map(function (d) { return DAYS[d] }).join(", ")
    case "monthly": return "monthly on the " + ordinal(rule.day)
    case "yearly":
        if (anchorMs === undefined || anchorMs === null)
            return "every year"
        var a = new Date(anchorMs)
        return "every year on " + MONTHS[a.getMonth()] + " " + a.getDate()
    case "every":
        var unit = rule.unit.slice(0, -1)
        return rule.n === 1 ? "every " + unit : "every " + rule.n + " " + rule.unit
    }
    return ""
}

// The muted line under a reminder's title: "every Mon, Wed, Fri · in 2 days" (just the relative
// text for a one-time reminder)
function subtitle(rule, anchorMs, relativeText) {
    return isValid(rule) ? describe(rule, anchorMs) + " · " + relativeText : relativeText
}

// A rule of the popup's kinds with sensible defaults taken from the due date
function defaultRule(kind, dueMs) {
    var due = new Date(dueMs)
    switch (kind) {
    case "daily": return { kind: "daily" }
    case "weekdays": return { kind: "weekdays" }
    case "weekly": return { kind: "weekly", days: [due.getDay()] }
    case "monthly": return { kind: "monthly", day: due.getDate() }
    case "yearly": return { kind: "yearly" }
    case "every": return { kind: "every", n: 2, unit: "hours" }
    }
    return null
}
