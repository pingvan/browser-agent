from __future__ import annotations


class LoopDetector:
    def detect_action_loop(self, browser_actions: list[str]) -> str:
        """Analyse ONLY browser actions (no meta actions like step_meta/save_memory)."""
        if len(browser_actions) >= 3:
            if browser_actions[-1] == browser_actions[-2] == browser_actions[-3]:
                return (
                    "LOOP: Same browser action 3 times in a row. "
                    "You MUST try a completely different element or approach."
                )

        if len(browser_actions) >= 4:
            a, b, c, d = browser_actions[-4:]
            if a == c and b == d and a != b:
                return (
                    "LOOP: Alternating between two actions without progress. "
                    "STOP this cycle. Use navigate to a different page, "
                    "or use save_memory to capture data before moving on."
                )

        for cycle_len in range(2, 5):
            if len(browser_actions) >= cycle_len * 2:
                recent = browser_actions[-cycle_len:]
                preceding = browser_actions[-cycle_len * 2 : -cycle_len]
                if recent == preceding:
                    return (
                        f"LOOP: {cycle_len}-action cycle repeating. "
                        f"Change your strategy fundamentally."
                    )

        return ""

    def detect_page_loop(self, fingerprints: list[str]) -> str:
        """Detect repeating cycles in page fingerprint history."""
        if len(fingerprints) < 4:
            return ""

        for cycle_len in range(2, 5):
            if len(fingerprints) < cycle_len * 2:
                continue
            recent = fingerprints[-cycle_len:]
            preceding = fingerprints[-cycle_len * 2 : -cycle_len]
            if recent == preceding:
                return (
                    f"LOOP: Same {cycle_len}-page cycle detected. "
                    f"You are visiting the same pages repeatedly. "
                    f"STOP. Save any data you need with save_memory, "
                    f"then navigate to a completely different page."
                )

        return ""

    def count_page_visits(self, fingerprints: list[str], current: str) -> int:
        """Count how many times the agent has visited the current page fingerprint."""
        if not current:
            return 0
        return fingerprints.count(current)
