import QtQuick
import "recurrence.js" as Recur

// The Repeat popup of the reminder pop-out (a Popover: a click-away layer that swallows the click
// that closes it, and a card that swallows its own clicks). Every control is a MouseArea.
//   ○ Never / ○ Every day / ○ Weekdays / ● Every week on [M T W T F S S] /
//   ○ Every month on the [1st ▾] / ○ Every year / ○ Custom: every [2] [hours ▾]
// Changes apply live: ruleChosen(rule) fires for every change (rule null = Never). The host owns the
// rule (bind `rule`) and the due date (`dueMs`: the default day of the month and the default weekday).
Popover {
    id: pop
    property var rule: null
    property real dueMs: 0
    signal ruleChosen(var rule)

    cardWidth: 400
    readonly property string kind: rule && rule.kind ? rule.kind : "never"
    readonly property bool hasMenuOpen: unitMenu.isOpen || dayMenu.isOpen
    // Monday first, like the pills
    readonly property var weekdayOrder: [1, 2, 3, 4, 5, 6, 0]
    readonly property var weekdayLetters: ({ 0: "S", 1: "M", 2: "T", 3: "W", 4: "T", 5: "F", 6: "S" })
    readonly property var weekdayNames: ({ 0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday" })

    function choose(kind) {
        if (kind === "never") {
            ruleChosen(null)
            return
        }
        if (pop.kind === kind && rule)
            return
        ruleChosen(Recur.defaultRule(kind, dueMs))
    }
    function togglePill(day) {
        if (pop.kind !== "weekly") {
            ruleChosen({ kind: "weekly", days: [day] })
            return
        }
        const days = Recur.toArray(rule.days)
        const at = days.indexOf(day)
        if (at >= 0) {
            if (days.length === 1)
                return              // at least one day stays selected
            days.splice(at, 1)
        } else {
            days.push(day)
        }
        ruleChosen({ kind: "weekly", days: Recur.sortedDays(days) })
    }
    function setMonthDay(day) { ruleChosen({ kind: "monthly", day: day }) }
    function setEvery(n, unit) { ruleChosen({ kind: "every", n: n, unit: unit }) }
    // Esc: a menu first, then the popup. Returns true when it used the key.
    function escapeKey() { return unitMenu.handleEscape() || dayMenu.handleEscape() || pop.handleEscape() }
    onClosed: { unitMenu.close(); dayMenu.close() }

    // Window coordinates of the controls (the self-test clicks them)
    function center(item) {
        const p = item.mapToItem(null, item.width / 2, item.height / 2)
        return { x: p.x, y: p.y }
    }
    function probe(name, index) {
        let item = ({ never: neverRow, daily: dailyRow, weekdays: weekdaysRow, weekly: weeklyRow, monthly: monthlyRow,
                      yearly: yearlyRow, custom: customRow, dayChip: dayChip, unitChip: unitChip, done: doneButton,
                      card: pop.card, stepperPlus: customStepper.plus, stepperMinus: customStepper.minus,
                      stepperField: customStepper.field })[name]
        if (!item && name === "pill") item = pills.itemAt(index)
        if (!item && name === "menuItem") item = (unitMenu.isOpen ? unitMenu : dayMenu).itemAtIndex(index)
        if (item && ["never", "daily", "weekdays", "weekly", "monthly", "yearly", "custom"].indexOf(name) >= 0) {
            const p = item.mapToItem(null, 44, item.height / 2)    // on the label, clear of the row's own controls
            return { x: p.x, y: p.y }
        }
        return item ? center(item) : null
    }
    function pillSelected(day) { return pop.kind === "weekly" && Recur.toArray(rule.days).indexOf(day) >= 0 }
    readonly property string unit: pop.kind === "every" ? rule.unit : "hours"
    readonly property int count: pop.kind === "every" ? rule.n : 2

    // One radio row: a circle (filled when selected) and a label, anywhere clickable.
    component RadioRow: Item {
        id: row
        property string label: ""
        property bool selected: false
        property bool clickable: true
        property alias labelItem: text
        signal clicked()
        width: parent.width
        height: 34
        Rectangle {
            id: dot
            anchors { left: parent.left; verticalCenter: parent.verticalCenter }
            width: 16; height: 16; radius: 8
            color: "transparent"
            border.width: row.selected ? 5 : 1.5
            border.color: row.selected ? Theme.accent : Theme.textMuted
            Behavior on border.width { NumberAnimation { duration: Theme.fast } }
        }
        Text {
            id: text
            anchors { left: dot.right; leftMargin: 12; verticalCenter: parent.verticalCenter }
            text: row.label
            font.family: Theme.font; font.pixelSize: Theme.sizeBody + 1
            color: Theme.text
        }
        MouseArea {
            anchors.fill: parent
            z: -1
            acceptedButtons: Qt.LeftButton
            enabled: row.clickable
            cursorShape: Qt.PointingHandCursor
            onClicked: row.clicked()
        }
    }

    RadioRow { id: neverRow; label: "Never"; selected: pop.kind === "never"; onClicked: pop.choose("never") }
    RadioRow { id: dailyRow; label: "Every day"; selected: pop.kind === "daily"; onClicked: pop.choose("daily") }
    RadioRow { id: weekdaysRow; label: "Weekdays (Mon–Fri)"; selected: pop.kind === "weekdays"; onClicked: pop.choose("weekdays") }

    RadioRow {
        id: weeklyRow
        label: "Every week on"
        selected: pop.kind === "weekly"
        onClicked: pop.choose("weekly")
        Row {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            spacing: 4
            Repeater {
                id: pills
                model: pop.weekdayOrder
                Rectangle {
                    id: pill
                    required property int modelData
                    readonly property bool on: pop.pillSelected(modelData)
                    width: 28; height: 28
                    radius: Theme.radius
                    color: on ? Theme.accent : (tap.pressed ? Theme.pressed : hover.hovered ? Theme.hover : Qt.rgba(1, 1, 1, 0.06))
                    border.width: on ? 0 : 1
                    border.color: Theme.border
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                    Text {
                        anchors.centerIn: parent
                        text: pop.weekdayLetters[pill.modelData]
                        font.family: Theme.font; font.pixelSize: Theme.sizeSmall; font.weight: Font.Medium
                        color: pill.on ? Theme.accentInk : (pop.kind === "weekly" ? Theme.text : Theme.textSecondary)
                    }
                    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                    MouseArea { id: tap; anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: pop.togglePill(pill.modelData) }
                }
            }
        }
    }

    RadioRow {
        id: monthlyRow
        label: "Every month on the"
        selected: pop.kind === "monthly"
        onClicked: pop.choose("monthly")
        InfoChip {
            id: dayChip
            anchors { left: parent.labelItem.right; leftMargin: 10; verticalCenter: parent.verticalCenter }
            compact: true
            label: Recur.ordinal(pop.kind === "monthly" ? pop.rule.day : new Date(pop.dueMs).getDate())
            glyph: Theme.icon.chevronDown
            onClicked: dayMenu.openAt(dayChip, 0, dayChip.height + 4)
        }
    }

    RadioRow { id: yearlyRow; label: "Every year"; selected: pop.kind === "yearly"; onClicked: pop.choose("yearly") }

    RadioRow {
        id: customRow
        label: "Custom: every"
        selected: pop.kind === "every"
        onClicked: pop.choose("every")
        Row {
            anchors { left: parent.labelItem.right; leftMargin: 10; verticalCenter: parent.verticalCenter }
            spacing: 8
            Stepper {
                id: customStepper
                anchors.verticalCenter: parent.verticalCenter
                value: pop.count
                minimum: 1; maximum: 99
                onValueEdited: (v) => pop.setEvery(v, pop.unit)
            }
            InfoChip {
                id: unitChip
                anchors.verticalCenter: parent.verticalCenter
                label: pop.unit
                glyph: Theme.icon.chevronDown
                onClicked: unitMenu.openAt(unitChip, 0, unitChip.height + 4)
            }
        }
    }

    Item {
        width: parent.width
        height: 34
        TextButton {
            id: doneButton
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            primary: true
            label: "Done"
            onClicked: pop.close()
        }
    }

    // The two small menus: the day of the month and the unit of a custom repeat
    ContextMenu {
        id: dayMenu
        cardWidth: 120
        model: Array.from({ length: 31 }, (_, i) => ({
            key: String(i + 1), label: Recur.ordinal(i + 1),
            glyph: pop.kind === "monthly" && pop.rule.day === i + 1 ? Theme.icon.check : ""
        }))
        onChosen: (key) => pop.setMonthDay(parseInt(key, 10))
    }
    ContextMenu {
        id: unitMenu
        cardWidth: 130
        model: ["hours", "days", "weeks"].map(u => ({ key: u, label: u, glyph: pop.unit === u && pop.kind === "every" ? Theme.icon.check : "" }))
        onChosen: (key) => pop.setEvery(pop.count, key)
    }
}
