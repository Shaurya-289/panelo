import QtQuick

// Pomodoro card for the Full App rail: the shared FocusTimer content on glass.
GlassPanel {
    id: card
    implicitHeight: timer.implicitHeight + Theme.pad * 2

    FocusTimer {
        id: timer
        x: Theme.pad; y: Theme.pad
        width: parent.width - Theme.pad * 2
    }
}
