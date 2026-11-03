"""Transient selections and request generations, not domain persistence."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class UiState:
    runtime_generation: int = 0
    generations: dict[str, int] = field(default_factory=dict)
    selections: dict[str, str | None] = field(default_factory=dict)

    def next_generation(self, key: str) -> int:
        self.generations[key] = self.generations.get(key, 0) + 1
        return self.generations[key]

    def replace_runtime(self) -> None:
        self.runtime_generation += 1
        self.generations.clear()
        self.selections.clear()
