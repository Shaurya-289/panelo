pragma Singleton
import QtQuick

// Design tokens for the prototype: dark, strong acrylic, neutral white accent,
// Outfit type, 6px corners, smooth-subtle motion.
QtObject {
    // Set from Python once the Windows backdrop request is known.
    property bool acrylic: true

    // Surfaces
    readonly property color base: acrylic ? Qt.rgba(0.06, 0.065, 0.08, 0.50) : "#121317"
    readonly property color panel: Qt.rgba(1, 1, 1, 0.055)
    readonly property color panelHover: Qt.rgba(1, 1, 1, 0.085)
    readonly property color raised: Qt.rgba(0.11, 0.115, 0.135, 0.94)
    readonly property color border: Qt.rgba(1, 1, 1, 0.10)
    readonly property color borderStrong: Qt.rgba(1, 1, 1, 0.22)
    readonly property color edgeHighlight: Qt.rgba(1, 1, 1, 0.14)
    readonly property color scrim: Qt.rgba(0, 0, 0, 0.45)

    // Text
    readonly property color text: "#f2f3f5"
    readonly property color textSecondary: Qt.rgba(1, 1, 1, 0.64)
    readonly property color textMuted: Qt.rgba(1, 1, 1, 0.40)

    // Neutral white accent
    readonly property color accent: "#ffffff"
    readonly property color accentInk: "#111216"
    readonly property color hover: Qt.rgba(1, 1, 1, 0.08)
    readonly property color pressed: Qt.rgba(1, 1, 1, 0.14)
    readonly property color selected: Qt.rgba(1, 1, 1, 0.11)
    readonly property color danger: "#e5484d"

    // Muted note tints (applied translucently over the glass)
    readonly property var tints: ({
        "none":  Qt.rgba(1, 1, 1, 0.055),
        "plum":  Qt.rgba(0.42, 0.26, 0.47, 0.42),
        "rose":  Qt.rgba(0.50, 0.25, 0.32, 0.42),
        "amber": Qt.rgba(0.50, 0.38, 0.16, 0.42),
        "moss":  Qt.rgba(0.29, 0.40, 0.24, 0.42),
        "teal":  Qt.rgba(0.18, 0.40, 0.42, 0.42),
        "slate": Qt.rgba(0.22, 0.30, 0.46, 0.42),
        "sand":  Qt.rgba(0.44, 0.40, 0.32, 0.40)
    })
    readonly property var tintOrder: ["none", "plum", "rose", "amber", "moss", "teal", "slate", "sand"]
    function tint(name) { return tints[name] !== undefined ? tints[name] : tints["none"] }

    // File kinds: muted badge colors (a bit stronger than the note tints), white text
    readonly property var kindColors: ({
        "pdf":     "#a8474c",
        "slides":  "#b4693a",
        "doc":     "#4470b3",
        "sheet":   "#3f8a58",
        "image":   "#8352a8",
        "video":   "#b04b7e",
        "audio":   "#2f8a8c",
        "archive": "#8f7b4c",
        "code":    "#4f5c77",
        "other":   "#6b6e76",
        "folder":  "#5a5e69"
    })
    function kindColor(kind) { return kindColors[kind] !== undefined ? kindColors[kind] : kindColors["other"] }

    // Shape & spacing (between balanced and compact)
    readonly property int radius: 6
    readonly property int gap: 10
    readonly property int pad: 14

    // Type
    readonly property string font: "Outfit"
    readonly property string iconFont: "Segoe Fluent Icons"
    readonly property int sizeSmall: 12
    readonly property int sizeBody: 13
    readonly property int sizeTitle: 15
    readonly property int sizeHeading: 18

    // Motion
    readonly property int fast: 150
    readonly property int normal: 220
    readonly property int easing: Easing.OutCubic

    // Segoe Fluent Icons glyphs
    readonly property var icon: ({
        backToWindow: "",
        menu: "", search: "", close: "", minimize: "",
        maximize: "", restore: "", add: "", pin: "",
        pinned: "", palette: "", archive: "", trash: "",
        play: "", pause: "", stop: "", check: "",
        note: "", tasks: "", reminder: "", focus: "",
        folder: "", settings: "", clock: "",
        checklist: "", back: "", more: "", skip: "",
        folderFilled: "", copy: "", paste: "", rename: "", newFolder: "", moveTo: "",
        showInFolder: "", gridView: "", listView: "", openFile: "",
        expand: "", chevronRight: "", chevronLeft: "", chevronDown: "", snooze: "",
        layout: "",
        repeat: "",
        calendar: "", flag: "", link: "",
        chevronUp: "", keyboard: "", folderOpen: "", reset: "", sound: "", cloud: ""
    })

    // Task priorities: the bar at the left of a row, the pills in the quick-add field
    readonly property color amber: "#e0a030"
    function priorityColor(name) {
        return name === "urgent" ? danger : name === "high" ? amber : name === "low" ? Qt.rgba(1, 1, 1, 0.38) : "transparent"
    }
    function priorityLabel(name) { return !name ? "" : name.charAt(0).toUpperCase() + name.slice(1) }
}
