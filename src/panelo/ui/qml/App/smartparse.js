.pragma library

// Smart-typing parser for the quick-add fields: "Call mom tomorrow 5pm !high".
//
// parse(text, now) -> {
//   title,        the text without the recognized parts
//   due,          ms epoch (local time) or null
//   hasTime,      the due has a time of day (false: a date only, stored at 00:00)
//   priority,     "low" | "normal" | "high" | "urgent" | null
//   spans,        every recognized part: [{ start, end, kind: "date" | "time" | "priority" }]
//   pills,        what the UI highlights: adjacent date and time parts merged into one pill
// }
// `now` is optional (ms epoch) so the tests can pin it.

var MONTHS = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
var WEEKDAYS = "monday|mon|tuesday|tues|tue|wednesday|weds|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun"
var DAY_PREFIX = "(?:\\b(?:on|by|due|for|before)\\s+)?"
var TIME_PREFIX = "(?:(?:\\bat|\\bby|\\bbefore)\\s+|@\\s*)?"

function startOfDay(ms) {
    var d = new Date(ms)
    d.setHours(0, 0, 0, 0)
    return d.getTime()
}

function addDays(ms, n) {
    var d = new Date(ms)
    d.setDate(d.getDate() + n)
    return d.getTime()
}

function monthIndex(word) {
    var key = word.slice(0, 3).toLowerCase()
    return ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].indexOf(key)
}

function weekdayIndex(word) {
    var key = word.slice(0, 3).toLowerCase()
    return ["sun", "mon", "tue", "wed", "thu", "fri", "sat"].indexOf(key)
}

// The next day-of-month/month on or after today (this year, else next year); null if invalid.
function pickDate(day, month, now) {
    var today = startOfDay(now)
    var year = new Date(today).getFullYear()
    if (month < 0 || month > 11 || day < 1 || day > 31)
        return null
    var candidate = new Date(year, month, day)
    if (candidate.getMonth() !== month)
        return null
    if (candidate.getTime() < today)
        candidate = new Date(year + 1, month, day)
    if (candidate.getMonth() !== month)
        return null
    return candidate.getTime()
}

function parse(text, nowArg) {
    var now = nowArg === undefined || nowArg === null ? Date.now() : nowArg
    var today = startOfDay(now)
    var spans = []
    var dayMs = null          // the chosen day (00:00), or null
    var clockH = null, clockM = null
    var exact = null          // "in 2 h": an exact moment
    var tonight = false
    var priority = null

    function taken(start, end) {
        for (var i = 0; i < spans.length; i++)
            if (start < spans[i].end && end > spans[i].start)
                return true
        return false
    }
    function scan(pattern, handle) {
        var re = new RegExp(pattern, "gi")
        var m
        while ((m = re.exec(text)) !== null) {
            if (m[0].length === 0) {
                re.lastIndex++
                continue
            }
            var start = m.index
            handle(m, start, m.index + m[0].length)
        }
    }
    function addSpan(kind, start, end) { spans.push({ start: start, end: end, kind: kind }) }

    // priorities: !low !high !urgent !normal !! (high) !!! (urgent)
    var priorityRe = /(^|\s)(!urgent|!high|!normal|!low|!!!|!!)(?=\s|$|[.,;])/gi
    var pm
    while ((pm = priorityRe.exec(text)) !== null) {
        var token = pm[2].toLowerCase()
        priority = token === "!urgent" || token === "!!!" ? "urgent"
                 : token === "!high" || token === "!!" ? "high"
                 : token === "!low" ? "low" : "normal"
        var s = pm.index + pm[1].length
        addSpan("priority", s, s + pm[2].length)
    }

    // in 30 min / in 2 h / in 3 days / in 2 weeks
    scan("\\bin\\s+(\\d+)\\s*(minutes?|mins?|min|m|hours?|hrs?|hr|h|days?|d|weeks?|wks?|wk|w)\\b", function (m, start, end) {
        if (taken(start, end) || exact !== null || dayMs !== null)
            return
        var n = parseInt(m[1], 10)
        var unit = m[2].toLowerCase().charAt(0)
        if (unit === "m") exact = now + n * 60000
        else if (unit === "h") exact = now + n * 3600000
        else dayMs = addDays(today, unit === "w" ? n * 7 : n)
        addSpan(unit === "m" || unit === "h" ? "time" : "date", start, end)
    })

    // next week (the next Monday)
    scan(DAY_PREFIX + "\\bnext\\s+week\\b", function (m, start, end) {
        if (taken(start, end) || dayMs !== null || exact !== null)
            return
        var weekday = new Date(today).getDay()
        dayMs = addDays(today, ((8 - weekday) % 7) || 7)
        addSpan("date", start, end)
    })

    // today / tonight / tomorrow
    scan(DAY_PREFIX + "\\b(today|tonight|tomorrow|tmrw|tmr)\\b", function (m, start, end) {
        if (taken(start, end) || dayMs !== null || exact !== null)
            return
        var word = m[1].toLowerCase()
        dayMs = word === "today" || word === "tonight" ? today : addDays(today, 1)
        if (word === "tonight")
            tonight = true
        addSpan("date", start, end)
    })

    // weekday names: the next one (after today)
    scan(DAY_PREFIX + "\\b(?:(?:next|this)\\s+)?(" + WEEKDAYS + ")\\b", function (m, start, end) {
        if (taken(start, end) || dayMs !== null || exact !== null)
            return
        var target = weekdayIndex(m[1])
        var ahead = (target - new Date(today).getDay() + 7) % 7
        dayMs = addDays(today, ahead === 0 ? 7 : ahead)
        addSpan("date", start, end)
    })

    // dates: "nov 5", "5 nov", "5th of november", "5/11" (day/month)
    scan(DAY_PREFIX + "\\b(" + MONTHS + ")\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?\\b(?!\\s*:)", function (m, start, end) {
        if (taken(start, end) || dayMs !== null || exact !== null)
            return
        var picked = pickDate(parseInt(m[2], 10), monthIndex(m[1]), now)
        if (picked === null)
            return
        dayMs = picked
        addSpan("date", start, end)
    })
    scan(DAY_PREFIX + "\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(?:of\\s+)?(" + MONTHS + ")\\b", function (m, start, end) {
        if (taken(start, end) || dayMs !== null || exact !== null)
            return
        var picked = pickDate(parseInt(m[1], 10), monthIndex(m[2]), now)
        if (picked === null)
            return
        dayMs = picked
        addSpan("date", start, end)
    })
    scan(DAY_PREFIX + "\\b(\\d{1,2})/(\\d{1,2})\\b(?!/)", function (m, start, end) {
        if (taken(start, end) || dayMs !== null || exact !== null)
            return
        var picked = pickDate(parseInt(m[1], 10), parseInt(m[2], 10) - 1, now)
        if (picked === null)
            return
        dayMs = picked
        addSpan("date", start, end)
    })

    // times: 5pm, 5:30pm, 17:00, at 9
    scan(TIME_PREFIX + "\\b(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)\\b", function (m, start, end) {
        if (taken(start, end) || clockH !== null || exact !== null)
            return
        var h = parseInt(m[1], 10), min = m[2] ? parseInt(m[2], 10) : 0
        if (h < 1 || h > 12 || min > 59)
            return
        clockH = (h % 12) + (m[3].toLowerCase() === "pm" ? 12 : 0)
        clockM = min
        addSpan("time", start, end)
    })
    scan(TIME_PREFIX + "\\b([01]?\\d|2[0-3]):([0-5]\\d)\\b", function (m, start, end) {
        if (taken(start, end) || clockH !== null || exact !== null)
            return
        clockH = parseInt(m[1], 10)
        clockM = parseInt(m[2], 10)
        addSpan("time", start, end)
    })
    // "at 9" is 09:00; at 1..6 a bare hour means the afternoon ("at 5" is 17:00)
    scan("\\bat\\s+(\\d{1,2})\\b(?![:/])", function (m, start, end) {
        if (taken(start, end) || clockH !== null || exact !== null)
            return
        var h = parseInt(m[1], 10)
        if (h > 23)
            return
        clockH = h >= 1 && h <= 6 ? h + 12 : h
        clockM = 0
        addSpan("time", start, end)
    })

    // Put the parts together
    var due = null, hasTime = false
    if (exact !== null) {
        due = exact
        hasTime = true
    } else {
        if (tonight && clockH === null) {
            clockH = 20
            clockM = 0
        }
        if (dayMs !== null) {
            due = dayMs
            if (clockH !== null) {
                var withTime = new Date(dayMs)
                withTime.setHours(clockH, clockM, 0, 0)
                due = withTime.getTime()
                hasTime = true
            }
        } else if (clockH !== null) {
            var at = new Date(today)
            at.setHours(clockH, clockM, 0, 0)
            due = at.getTime()
            if (due <= now)
                due = addDays(due, 1)
            hasTime = true
        }
    }

    // The title: the text without the recognized parts
    spans.sort(function (a, b) { return a.start - b.start })
    var title = ""
    var cursor = 0
    for (var i = 0; i < spans.length; i++) {
        title += text.slice(cursor, spans[i].start) + " "
        cursor = spans[i].end
    }
    title += text.slice(cursor)
    title = title.replace(/\s+/g, " ").replace(/^[\s,;:-]+|[\s,;:-]+$/g, "")

    // Pills: a date and a time part with only blanks between them read as one.
    var pills = []
    for (var j = 0; j < spans.length; j++) {
        var span = spans[j]
        var last = pills.length ? pills[pills.length - 1] : null
        var isDateish = span.kind !== "priority"
        if (last && last.kind !== "priority" && isDateish && /^\s*$/.test(text.slice(last.end, span.start)))
            last.end = span.end
        else
            pills.push({ start: span.start, end: span.end, kind: span.kind === "priority" ? "priority" : "date",
                         value: span.kind === "priority" ? priority : "" })
    }

    return { title: title, due: due, hasTime: hasTime, priority: priority, spans: spans, pills: pills }
}
