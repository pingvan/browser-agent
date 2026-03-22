"""PlanTracker — mutable execution plan maintained by the agent.

The agent creates a plan after the first page observation and updates it
as steps are completed or circumstances change. The plan is injected into
every LLM context window as a pinned block, giving the model persistent
awareness of what remains to be done.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanTracker:
    steps: list[str]
    completed: set[int] = field(default_factory=set)
    skipped: set[int] = field(default_factory=set)
    current: int | None = field(default=None)
    notes: str = ""

    def mark_completed(self, indices: list[int]) -> None:
        """Mark the given step indices (0-based) as completed."""
        for i in indices:
            if 0 <= i < len(self.steps):
                self.completed.add(i)

    def set_current(self, index: int) -> None:
        """Set the current active step index (0-based)."""
        if 0 <= index < len(self.steps):
            self.current = index

    def revise_remaining(self, new_steps: list[str]) -> None:
        """Replace all uncompleted steps with *new_steps*.

        Completed steps are preserved at the front in their original order;
        their indices are remapped to 0..N-1.
        """
        completed_pairs = sorted((i, self.steps[i]) for i in self.completed if i < len(self.steps))
        self.steps = [s for _, s in completed_pairs] + new_steps
        self.completed = set(range(len(completed_pairs)))
        self.skipped = set()
        self.current = None

    def add_notes(self, text: str) -> None:
        if self.notes:
            self.notes = f"{self.notes}\n{text}"
        else:
            self.notes = text

    def render(self, step: int) -> str:
        lines = [f"## Current Plan (step {step})", "Steps:"]
        for i, s in enumerate(self.steps):
            if i in self.completed:
                mark = "[x]"
            elif i == self.current:
                mark = "[>]"
            elif i in self.skipped:
                mark = "[-]"
            else:
                mark = "[ ]"
            lines.append(f"  {mark} {i + 1}. {s}")
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        return "\n".join(lines)
