from __future__ import annotations

import json
from typing import Any

from src.agent.schema import AgentOutput
from src.agent.state import (
    AgentState,
    InspectionResult,
    render_memory,
)
from src.config.settings import (
    MAX_PROMPT_ELEMENTS,
    MAX_STEPS,
)

# Approximate token limit before triggering compression.
_DEFAULT_TOKEN_LIMIT = 90_000

# Number of recent cycles to keep intact during compression.
_DEFAULT_KEEP_RECENT = 6


class MessageManager:
    """Stateful conversation accumulator for multi-turn tool-calling."""

    def __init__(self) -> None:
        self.conversation: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public: append messages
    # ------------------------------------------------------------------

    def add_observation(self, state: AgentState, screenshot_b64: str = "") -> None:
        """Append a user message with the current page observation (used for initial step)."""
        text = self._build_observation_text(state)
        self.conversation.append({"role": "user", "content": self._build_content(text, screenshot_b64)})

    def add_agent_output(self, output: AgentOutput) -> None:
        """Append an assistant message with the structured JSON output."""
        self.conversation.append({"role": "assistant", "content": output.model_dump_json()})

    def add_action_results(
        self,
        results: list[dict[str, Any]],
        state: AgentState,
        screenshot_b64: str = "",
    ) -> None:
        """Append a user message combining action results with page observation."""
        result_lines = [
            f"- {r['tool']}: {'OK' if r.get('success') else 'FAILED'}. {str(r.get('description', ''))[:150]}"
            for r in results
        ]
        result_summary = "## Action Results\n" + "\n".join(result_lines) if result_lines else ""
        obs_text = self._build_observation_text(state)
        text = (result_summary + "\n\n" + obs_text).strip()
        self.conversation.append({"role": "user", "content": self._build_content(text, screenshot_b64)})

    # ------------------------------------------------------------------
    # Public: build final messages list for the API call
    # ------------------------------------------------------------------

    def build_messages(self, *, system_prompt: str) -> list[dict[str, Any]]:
        return [{"role": "system", "content": system_prompt}] + self.conversation

    # ------------------------------------------------------------------
    # Public: compression
    # ------------------------------------------------------------------

    def compress_if_needed(self, token_limit: int = _DEFAULT_TOKEN_LIMIT, task: str = "") -> None:
        """Compress old cycles when the conversation grows too large."""
        estimated_tokens = len(json.dumps(self.conversation, ensure_ascii=False)) // 4
        if estimated_tokens <= token_limit:
            return

        keep = _DEFAULT_KEEP_RECENT
        while keep >= 2:
            self.compress_old_steps(keep_recent=keep, task=task)
            estimated_tokens = len(json.dumps(self.conversation, ensure_ascii=False)) // 4
            if estimated_tokens <= token_limit:
                return
            keep -= 1

    def compress_old_steps(self, keep_recent: int = _DEFAULT_KEEP_RECENT, task: str = "") -> None:
        """Replace old conversation cycles with a compact text summary.

        A pinned task-reminder message is always kept at position 0 so the
        model never loses sight of the original task, even after multiple
        compression passes.
        """
        # Strip existing pinned task reminder before splitting so it is never
        # treated as a compressible cycle.
        existing_pinned: dict[str, Any] | None = None
        if self.conversation and self._is_task_reminder(self.conversation[0]):
            existing_pinned = self.conversation[0]
            self.conversation = self.conversation[1:]

        cycles = self._split_into_cycles()
        if len(cycles) <= keep_recent:
            # Nothing to compress — restore the pinned message and exit.
            if existing_pinned is not None:
                self.conversation.insert(0, existing_pinned)
            return

        old_cycles = cycles[:-keep_recent]
        recent_cycles = cycles[-keep_recent:]

        summary_lines = [f"[Context from {len(old_cycles)} compressed earlier steps]"]
        for cycle in old_cycles:
            summary_lines.append(f"- {self._extract_action_from_cycle(cycle)}")

        summary_message: dict[str, Any] = {
            "role": "user",
            "content": "\n".join(summary_lines),
        }

        # Rebuild: pinned task reminder → summary → recent cycles.
        self.conversation = []
        if task:
            self.conversation.append({
                "role": "user",
                "content": (
                    "YOUR TASK (never lose track of this):\n"
                    f"{task}\n"
                    "Execute this task."
                ),
            })
        elif existing_pinned is not None:
            self.conversation.append(existing_pinned)
        self.conversation.append(summary_message)
        for cycle in recent_cycles:
            self.conversation.extend(cycle)

    # ------------------------------------------------------------------
    # Internal: content builder helper
    # ------------------------------------------------------------------

    def _build_content(self, text: str, screenshot_b64: str) -> Any:
        if screenshot_b64:
            return [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_b64}",
                        "detail": "high",
                    },
                },
            ]
        return text

    # ------------------------------------------------------------------
    # Internal: observation text builder
    # ------------------------------------------------------------------

    def _build_observation_text(self, state: AgentState) -> str:
        sections: list[str] = []

        # Forced instruction (escalating retry nudge) — prepended before everything else.
        forced = state.get("forced_instruction", "")
        if forced:
            sections.extend(["!! SYSTEM INSTRUCTION !!", forced, "!! END SYSTEM INSTRUCTION !!", ""])
            state["forced_instruction"] = ""  # show once

        # Task — always first and most prominent so the model treats it as a command.
        task = state.get("task", "")
        if task:
            sections.extend(
                [
                    "================================================================",
                    "YOUR TASK (execute this, do not ask the user to repeat it):",
                    task,
                    "================================================================",
                    "",
                ]
            )

        # Durable memory — always present so model sees cross-step data.
        memory_text = render_memory(state.get("memory", []))
        if memory_text and memory_text != "(memory is empty)":
            sections.extend(["## Durable Memory", memory_text, ""])

        # Step counter, URL, title
        sections.extend(
            [
                f"## Step {state.get('step_count', 0) + 1}/{MAX_STEPS}",
                f"URL: {state.get('current_url', '')}",
                f"Title: {state.get('page_title', '')}",
            ]
        )

        subtask = state.get("current_subtask", "")
        if subtask:
            sections.append(f"Subtask: {subtask}")

        # Visible page text
        excerpt = state.get("page_text_excerpt", "")
        if excerpt:
            sections.extend(["", "## Visible Page Text", excerpt])

        # DOM inspection result (if available for this page)
        dom_inspection = state.get("last_dom_inspection")
        if dom_inspection:
            inspection_text = self._render_inspection(dom_inspection)
            if inspection_text:
                sections.extend(["", "## DOM Inspection", inspection_text])

        # Interactive elements
        sections.extend(["", "## Interactive Elements", self._render_elements(state)])

        # Errors / warnings / hints
        last_error = state.get("last_error", "")
        if last_error:
            sections.extend(["", "## Last Error", last_error])
        stuck_hint = state.get("stuck_hint", "")
        if stuck_hint:
            sections.extend(["", "## ⚠ Loop Warning", stuck_hint])

        if state.get("overlay_click_blocked"):
            sections.extend([
                "",
                "## !! OVERLAY BLOCKING CLICKS",
                "Your last click was blocked because an overlay/popup/modal is covering the page.",
                "You MUST handle the overlay first:",
                "  - Look at the screenshot for cookie consent, popup, or modal",
                "  - Find and click the accept/close/dismiss button",
                "  - Or try press_key('Escape')",
                "  - Do NOT try to click elements behind the overlay",
            ])

        phase_switch_warning = state.get("phase_switch_warning", "")
        if phase_switch_warning:
            sections.extend(["", "## ⚠ Phase Switch Warning", phase_switch_warning])
            state["phase_switch_warning"] = ""  # show once

        injection_warnings = state.get("prompt_injection_warnings", [])
        if injection_warnings:
            sections.extend(
                [
                    "",
                    "## Security Warnings",
                    "\n".join(f"- {w}" for w in injection_warnings[:4]),
                ]
            )

        # User response (from ask_user)
        user_response = state.get("user_response")
        if user_response:
            sections.extend(["", "## Last User Response", user_response])

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Internal: compression helpers
    # ------------------------------------------------------------------

    def _split_into_cycles(self) -> list[list[dict[str, Any]]]:
        """Split conversation into cycles: each cycle starts with a user message."""
        cycles: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []

        for msg in self.conversation:
            if msg.get("role") == "user" and current:
                cycles.append(current)
                current = []
            current.append(msg)

        if current:
            cycles.append(current)
        return cycles

    def _is_task_reminder(self, msg: dict[str, Any]) -> bool:
        """Return True if *msg* is a pinned task-reminder injected by compression."""
        if msg.get("role") != "user":
            return False
        content = msg.get("content", "")
        return isinstance(content, str) and content.startswith("YOUR TASK (never lose track of this):")

    def _extract_action_from_cycle(self, cycle: list[dict[str, Any]]) -> str:
        """Build a rich one-line summary from a conversation cycle (new JSON format)."""
        url = ""
        title = ""
        user_msg = cycle[0] if cycle else {}
        user_content = user_msg.get("content", "")
        if isinstance(user_content, list):
            for part in user_content:
                if isinstance(part, dict) and part.get("type") == "text":
                    user_content = part.get("text", "")
                    break
            else:
                user_content = ""
        if isinstance(user_content, str):
            for line in user_content.split("\n"):
                if line.startswith("URL: "):
                    url = line[5:].strip()[:80]
                elif line.startswith("Title: "):
                    title = line[7:].strip()[:60]

        actions: list[str] = []
        outcomes: list[str] = []

        for msg in cycle:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    try:
                        data = json.loads(content)
                        next_goal = data.get("next_goal", "")
                        for act in data.get("action", []):
                            name = act.get("tool_name", "?")
                            args = act.get("arguments", {})
                            if name == "click":
                                actions.append(f"click(#{args.get('element_id', '?')})")
                            elif name == "type_text":
                                text = str(args.get("text", ""))[:30]
                                actions.append(f'type("{text}")')
                            elif name == "navigate":
                                actions.append(f"navigate({args.get('url', '?')[:50]})")
                            elif name == "save_memory":
                                key = str(args.get("key", "?"))
                                value = str(args.get("value", ""))[:60]
                                outcomes.append(f'saved {key}="{value}"')
                            elif name == "ask_user":
                                q = str(args.get("question", ""))[:60]
                                actions.append(f'ask_user("{q}")')
                            elif name == "done":
                                actions.append("done")
                            else:
                                actions.append(name)
                        if next_goal:
                            outcomes.append(f'goal="{next_goal[:60]}"')
                    except (json.JSONDecodeError, TypeError):
                        pass
            elif msg.get("role") == "user":
                # Extract failures from "## Action Results" section in subsequent user messages.
                content = msg.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            content = part.get("text", "")
                            break
                    else:
                        content = ""
                if isinstance(content, str) and "FAILED" in content:
                    for line in content.split("\n"):
                        if ": FAILED." in line:
                            desc = line.split(": FAILED.", 1)[-1].strip()[:60]
                            outcomes.append(f"FAILED: {desc}")

        action_str = ", ".join(actions) if actions else "no_action"
        page_info = title or url or "unknown page"
        outcome_str = f" | {', '.join(outcomes)}" if outcomes else ""
        return f"{action_str} on '{page_info}'{outcome_str}"

    # ------------------------------------------------------------------
    # Internal: rendering helpers
    # ------------------------------------------------------------------

    def _render_inspection(self, inspection: InspectionResult) -> str:
        question = str(inspection.get("question", "")).strip()
        answer = str(inspection.get("answer", "")).strip()
        observations = inspection.get("observations", [])
        candidates = inspection.get("candidate_elements", [])
        if not (question or answer or observations or candidates):
            return ""

        lines: list[str] = []
        if question:
            lines.append(f"Question: {question}")
        if answer:
            lines.append(f"Answer: {answer}")
        if observations:
            lines.append("Observations: " + " | ".join(str(o) for o in observations[:4]))
        if candidates:
            rendered = []
            for c in candidates[:5]:
                rendered.append(f'[{c.get("element_id", "?")}] {str(c.get("reason", "")).strip()}')
            lines.append("DOM candidates: " + " | ".join(rendered))
        return "\n".join(lines)

    def _render_elements(self, state: AgentState) -> str:
        lines: list[str] = []
        for element in state.get("interactive_elements", [])[:MAX_PROMPT_ELEMENTS]:
            label = (
                element.get("aria_label")
                or element.get("text")
                or element.get("placeholder")
                or "(no label)"
            )
            role = element.get("role") or element.get("tag") or "element"
            extra: list[str] = []
            if element.get("href"):
                extra.append(f'href="{element.get("href", "")}"')
            if element.get("value"):
                extra.append(f'value="{element.get("value", "")}"')
            if element.get("disabled"):
                extra.append("disabled")
            suffix = f" | {' | '.join(extra)}" if extra else ""
            lines.append(
                f'[{element.get("index", element.get("ref", "?"))}] {role} | "{label}"{suffix}'
            )
        return "\n".join(lines) if lines else "(no interactive elements)"
