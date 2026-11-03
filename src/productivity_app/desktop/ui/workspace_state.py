"""Canonical integer tile geometry, separate from temporary presentations."""

from dataclasses import dataclass

PANEL_IDS = ("notes", "todo", "files", "timers", "reminders")
MINIMA = {
    "notes": (320, 240),
    "todo": (260, 180),
    "files": (240, 180),
    "timers": (220, 180),
    "reminders": (240, 180),
}


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


def default_layout() -> dict[str, Rect]:
    return {
        "notes": Rect(0, 0, 507, 660),
        "todo": Rect(521, 0, 361, 323),
        "files": Rect(521, 337, 361, 323),
        "timers": Rect(896, 0, 284, 323),
        "reminders": Rect(896, 337, 284, 323),
    }
