import json
from typing import Any


class LoopDetector:
    def __init__(self) -> None:
        self.action_history: list[str] = []

    def record_action(self, name: str, args: dict[str, Any]) -> None:
        signature = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        self.action_history.append(signature)

    def is_stuck(self) -> bool:
        history = self.action_history
        if len(history) >= 4:
            last = history[-4:]
            if last[0] == last[2] and last[1] == last[3]:
                return True
        if len(history) >= 3:
            last = history[-3:]
            if last[0] == last[1] == last[2]:
                return True
        return False

    def get_unstuck_hint(self) -> str:
        return (
            "You seem to be repeating the same actions in a loop. "
            "Try a completely different approach: use different elements, "
            "navigate to a different page, or re-read the page state to find "
            "an alternative path to complete the task."
        )

    def reset(self) -> None:
        self.action_history.clear()
