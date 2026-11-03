"""Visual tokens, including provisional responsive thresholds."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget

COMPACT_ENTER_WIDTH = 1040
WIDE_RETURN_WIDTH = 1072
WORKSPACE_MARGIN = 20
PANEL_SPACING = 14
PANEL_PADDING = 14
SHELL_TINT = "rgba(13, 17, 23, 115)"
OPAQUE_TINT = "rgb(13, 17, 23)"

# Palette: neutral blue-black surfaces, one indigo accent, three text levels.
#   surface #14171d / raised #1b1f27 / border rgba(255,255,255,0.09)
#   text #e8ebf1 / secondary #98a2b3 / muted #6b7587 / accent #7c8cff
# Scope surfaces so transparent containers do not acquire opaque backgrounds.
DASHBOARD_STYLE = """
QFrame#dashboardPanel {
    background: rgba(20, 23, 29, 238);
    border: 1px solid rgba(255, 255, 255, 22); border-radius: 14px;
}
QFrame#dashboardPanel[invalidDrop="true"] { border: 2px solid #f07178; }
QFrame#inputSheet { background: #1b1f27; border: 1px solid #7c8cff; border-radius: 10px; }
QLabel { color: #e8ebf1; background: transparent; border: none; }
QLabel[role="panelTitle"] { color: #98a2b3; font-weight: 600; letter-spacing: 0.5px; }
QLabel[role="secondary"] { color: #98a2b3; }
QLabel[role="timerDisplay"] { font-size: 40px; font-weight: 300; color: #ffffff; }
QCheckBox { color: #cdd3de; background: transparent; spacing: 8px; }
QCheckBox:disabled { color: #6b7587; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid rgba(255, 255, 255, 60); background: rgba(255, 255, 255, 6);
}
QCheckBox::indicator:hover { border-color: #7c8cff; }
QCheckBox::indicator:checked { background: #7c8cff; border-color: #7c8cff; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
    color: #e8ebf1; background: rgba(255, 255, 255, 8);
    border: 1px solid rgba(255, 255, 255, 26); border-radius: 8px; padding: 8px 10px;
    selection-background-color: #4a57b8; selection-color: #ffffff;
}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover {
    border-color: rgba(255, 255, 255, 52);
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #7c8cff; background: rgba(124, 140, 255, 14);
}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: #6b7587; background: rgba(255, 255, 255, 3); border-color: rgba(255, 255, 255, 12);
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #1b1f27; color: #e8ebf1; border: 1px solid #2c323f;
    selection-background-color: #333c66; selection-color: #ffffff; outline: none;
}
QListWidget { color: #d6dbe5; background: transparent; border: none; padding: 0; outline: none; }
QListWidget::item { padding: 9px 8px; border: none; border-radius: 7px; }
QListWidget::item:hover { background: rgba(255, 255, 255, 9); }
QListWidget::item:selected { background: rgba(124, 140, 255, 46); color: #ffffff; }
QLineEdit#recordTitle {
    font-size: 18px; font-weight: 600; color: #ffffff;
    background: transparent; border: none; padding: 8px 0;
}
QPlainTextEdit#recordBody { color: #d6dbe5; background: transparent; border: none; padding: 8px 0; }
QMenu { color: #d6dbe5; background: #1b1f27; border: 1px solid #2c323f; border-radius: 9px; padding: 6px; }
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background: #333c66; color: #ffffff; }
QMenu::item:disabled { color: #6b7587; }
QMenu::separator { height: 1px; background: #2c323f; margin: 4px 6px; }
QPushButton, QToolButton {
    color: #dfe3ec; background: rgba(255, 255, 255, 10);
    border: 1px solid rgba(255, 255, 255, 22); border-radius: 8px; padding: 7px 14px;
}
QPushButton:hover, QToolButton:hover {
    background: rgba(124, 140, 255, 38); border-color: rgba(124, 140, 255, 140); color: #ffffff;
}
QPushButton:pressed, QToolButton:pressed { background: rgba(124, 140, 255, 70); }
QPushButton:checked, QToolButton:checked {
    background: #7c8cff; border-color: #7c8cff; color: #10131a;
}
QPushButton:default { background: #7c8cff; border-color: #7c8cff; color: #10131a; font-weight: 600; }
QPushButton:default:hover { background: #929fff; }
QPushButton:focus, QToolButton:focus { border: 1px solid #7c8cff; }
QPushButton:disabled, QToolButton:disabled {
    color: #6b7587; background: rgba(255, 255, 255, 4); border-color: rgba(255, 255, 255, 12);
}
QToolTip { color: #e8ebf1; background: #1b1f27; border: 1px solid #2c323f; padding: 4px 8px; }
QScrollArea, QWidget#dashboardCanvas { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255, 255, 255, 40); border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 80); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: rgba(255, 255, 255, 40); border-radius: 3px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
"""


def apply_dashboard_theme(widget: QWidget) -> None:
    widget.setFont(QFont("Segoe UI", 10))
    widget.setStyleSheet(DASHBOARD_STYLE)
