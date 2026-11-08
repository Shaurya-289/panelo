import QtQuick

// A number stepper: − and + buttons (one step each) around an editable value. The parent owns the
// value: it binds `value` and handles valueEdited(v), which already respects minimum and maximum.
Row {
    id: stepper
    property int value: 0
    property int minimum: 1
    property int maximum: 180
    property int step: 1
    property string suffix: ""
    property alias field: box
    property alias minus: minusButton
    property alias plus: plusButton
    signal valueEdited(int value)

    spacing: 6

    function commit() {
        const n = parseInt(box.text, 10)
        const clamped = isNaN(n) ? stepper.value : Math.max(stepper.minimum, Math.min(stepper.maximum, n))
        box.text = String(clamped)
        if (clamped !== stepper.value)
            stepper.valueEdited(clamped)
    }
    function nudge(delta) {
        const next = Math.max(minimum, Math.min(maximum, value + delta))
        if (next !== value)
            valueEdited(next)
    }
    onValueChanged: if (!box.input.activeFocus) box.text = String(value)

    component StepButton: Rectangle {
        id: button
        property string label: ""
        property bool enabledStep: true
        signal clicked()
        width: 28; height: 28
        radius: Theme.radius
        color: tap.pressed ? Theme.pressed : hover.hovered ? Theme.hover : Qt.rgba(1, 1, 1, 0.05)
        border.width: 1
        border.color: Theme.border
        opacity: enabledStep ? 1 : 0.4
        Text {
            anchors.centerIn: parent
            text: button.label
            font.family: Theme.font; font.pixelSize: 16
            color: Theme.text
        }
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        MouseArea { id: tap; anchors.fill: parent; acceptedButtons: Qt.LeftButton; onClicked: button.clicked() }
    }

    StepButton { id: minusButton; label: "−"; enabledStep: stepper.value > stepper.minimum; onClicked: stepper.nudge(-stepper.step) }
    Rectangle {
        width: suffixText.visible ? 74 : 52; height: 28
        radius: Theme.radius
        color: Qt.rgba(1, 1, 1, 0.05)
        border.width: 1
        border.color: box.input.activeFocus ? Theme.borderStrong : Theme.border
        TextBox {
            id: box
            anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
            width: 28
            multiline: false
            text: String(stepper.value)
            onSubmitted: stepper.commit()
            Component.onCompleted: input.activeFocusChanged.connect(() => { if (!input.activeFocus) stepper.commit() })
        }
        Text {
            id: suffixText
            visible: stepper.suffix !== ""
            anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
            text: stepper.suffix
            font.family: Theme.font; font.pixelSize: Theme.sizeSmall
            color: Theme.textMuted
        }
        MouseArea { anchors.fill: parent; z: -1; acceptedButtons: Qt.LeftButton; onClicked: box.input.forceActiveFocus() }
    }
    StepButton { id: plusButton; label: "+"; enabledStep: stepper.value < stepper.maximum; onClicked: stepper.nudge(stepper.step) }
}
