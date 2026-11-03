"""A single presentation clock: no domain calls, countdown engine or persistence."""

from collections.abc import Callable
from time import monotonic_ns
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal


class TimerDisplay(QObject):
    painted = Signal(str)

    def __init__(
        self, parent: QObject | None = None, now_ns: Callable[[], int] = monotonic_ns
    ) -> None:
        super().__init__(parent)
        self.now_ns = now_ns
        self.view: Any = None
        self.captured_ns = 0
        self.visible = False
        self.tick = QTimer(self)
        self.tick.setInterval(1000)
        self.tick.timeout.connect(self.repaint)

    def update(self, view: Any) -> None:
        self.view = view
        self.captured_ns = self.now_ns()
        self.set_visible(self.visible)
        self.repaint()

    def remaining(self) -> int:
        if self.view is None:
            return 0
        remaining = int(self.view.remaining_ms)
        if self.view.state == "RUNNING":
            remaining -= max(0, self.now_ns() - self.captured_ns) // 1_000_000
        return max(0, remaining)

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if (
            visible
            and self.view is not None
            and self.view.state == "RUNNING"
            and self.remaining() > 0
        ):
            self.tick.start()
        else:
            self.tick.stop()

    def text(self) -> str:
        if self.view is None:
            return "No active timer"
        remaining = self.remaining()
        if self.view.state == "RUNNING" and remaining == 0:
            return f"{self.view.name} · Elapsed · Awaiting recording"
        seconds = (remaining + 999) // 1000
        label = "Paused" if self.view.state == "PAUSED" else self.view.effective_state.title()
        return f"{self.view.name} · {seconds // 60:02d}:{seconds % 60:02d} · {label}"

    def repaint(self) -> None:
        self.painted.emit(self.text())
        if self.remaining() == 0:
            self.tick.stop()
