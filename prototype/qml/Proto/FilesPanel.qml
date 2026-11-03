import QtQuick

// The overlay's Files panel: a compact file list with drill-in folders and a breadcrumb.
// Double-clicking the header expands to the Full App's Files view at the same folder.
OverlayPanel {
    id: panel
    title: "Files"
    property string query: ""
    property string rel: ""
    property alias browser: browser
    signal clearQueryRequested()
    signal launched()

    FileBrowser {
        id: browser
        anchors.fill: parent
        mode: "list"
        query: panel.query
        rel: panel.rel
        onNavigated: (target) => panel.rel = target
        onClearQueryRequested: panel.clearQueryRequested()
        onLaunched: panel.launched()
    }
}
