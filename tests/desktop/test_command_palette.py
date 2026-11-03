from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from productivity_app.desktop.ui.command_palette import CommandPalette


def test_history_output_bounds_error_retention_and_fixed_completion(qapplication):
    palette = CommandPalette()
    calls = []
    palette.execute.connect(calls.append)
    for index in range(105):
        palette.input.setText(f"help {index}")
        palette.submit()
    assert len(palette.history) == 100 and len(calls) == 105
    QTest.keyClick(palette.input, Qt.Key.Key_Up)
    assert palette.input.text() == "help 104"
    QTest.keyClick(palette.input, Qt.Key.Key_Down)
    assert palette.input.text() == ""
    palette.input.setText("bad input")
    palette.report("Rejected", False)
    assert palette.input.text() == "bad input"
    for index in range(205):
        palette.report(f"Result {index}")
    assert len(palette.outputs) == 200
    palette.input.setText("remind")
    QTest.keyClick(palette.input, Qt.Key.Key_Tab)
    assert palette.input.text() == "reminders"
    palette.input.setText('search "remind')
    QTest.keyClick(palette.input, Qt.Key.Key_Tab)
    assert palette.input.text() == 'search "remind'
    palette.deleteLater()
