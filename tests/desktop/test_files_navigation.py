import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from productivity_app.desktop.ui.files import FilesPanel, ParentTabButton


def test_files_exact_immediate_parent_navigation(qapplication: QApplication) -> None:
    panel = FilesPanel()
    panel.show()
    assert tuple(panel.folder_buttons) == ("study", "coding", "personal")
    assert panel.parent_tab.isHidden()
    assert len(panel.findChildren(ParentTabButton)) == 1
    panel.folder_buttons["study"].click()
    assert tuple(panel.folder_buttons) == ("semester1", "reading")
    assert panel.parent_tab.text() == "Study"
    assert panel.parent_tab.accessibleName() == "Back to root"
    panel.folder_buttons["semester1"].click()
    assert panel.parent_tab.text() == "Study"
    panel.folder_buttons["maths"].click()
    assert panel.parent_tab.text() == "Semester 1"
    assert not panel.folder_buttons
    for expected in ("semester1", "study", None):
        panel.parent_tab.click()
        assert panel.current_folder_id == expected
    assert panel.parent_tab.isHidden()
    panel.go_parent()
    assert panel.current_folder_id is None
    panel.close()
    panel.deleteLater()


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_files_keyboard_navigation_and_invalid_jump(qapplication: QApplication, key: Qt.Key) -> None:
    panel = FilesPanel()
    panel.show()
    panel.folder_buttons["study"].setFocus()
    QTest.keyClick(panel.folder_buttons["study"], key)
    assert panel.current_folder_id == "study"
    panel.parent_tab.setFocus()
    QTest.keyClick(panel.parent_tab, key)
    assert panel.current_folder_id is None
    with pytest.raises(ValueError):
        panel.enter_folder("maths")
    for folder_id in ("coding", "personal"):
        panel.enter_folder(folder_id)
        assert not panel.folder_buttons
        assert panel.parent_tab.accessibleName() == "Back to root"
        panel.go_parent()
    panel.close()
    panel.deleteLater()
