from __future__ import annotations

import re
from typing import Any

from src.config.settings import MAIN_MODEL, MODEL_PRICING
from src.utils.logger import logger

_SEP = "=" * 80


class StepLogger:
    """Structured per-step logging for the browser agent."""

    def __init__(self) -> None:
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_cost: float = 0.0
        self.step_durations: list[int] = []
        self.memory_save_count: int = 0
        self.loop_warning_count: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    # ------------------------------------------------------------------
    # Public: per-step structured block
    # ------------------------------------------------------------------

    def log_step(
        self,
        *,
        step: int,
        max_steps: int,
        url: str,
        title: str,
        subtask: str,
        element_count: int,
        plan_value: str | None,
        tool_calls_info: list[dict[str, Any]],
        memory_ops: list[dict[str, str]],
        loop_warning: str,
        prompt_tokens: int,
        completion_tokens: int,
        step_duration_ms: int,
        evaluation: str,
        reasoning_memory: str,
        next_goal: str,
    ) -> None:
        step_cost, cost_known = self._compute_cost(prompt_tokens, completion_tokens)
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_cost += step_cost
        self.step_durations.append(step_duration_ms)

        lines: list[str] = [_SEP]
        subtask_part = f" | Subtask: {subtask[:60]}" if subtask else ""
        lines.append(f"STEP {step}/{max_steps} | URL: {url[:80]}{subtask_part}")
        lines.append(_SEP)

        lines.extend(["", "PAGE STATE:"])
        lines.append(f"  Title: {title[:100]}")
        lines.append(f"  Elements: {element_count} interactive")

        if evaluation or reasoning_memory or next_goal:
            lines.extend(["", "AGENT REASONING:"])
            if evaluation:
                lines.extend(_wrap(f"Eval: {evaluation}", width=76, indent="  "))
            if reasoning_memory:
                lines.extend(_wrap(f"Memory: {reasoning_memory}", width=76, indent="  "))
            if next_goal:
                lines.extend(_wrap(f"Next: {next_goal}", width=76, indent="  "))

        plan_lines = self._format_plan_status(plan_value, subtask)
        if plan_lines:
            lines.extend(["", "PLAN STATUS:"])
            for pl in plan_lines:
                lines.append(f"  {pl}")

        if tool_calls_info:
            heading = "ACTION CHOSEN:" if len(tool_calls_info) == 1 else "ACTIONS CHOSEN:"
            lines.extend(["", heading])
            for tc in tool_calls_info:
                lines.append(f"  Tool: {tc.get('name', '?')}")
                args_str = tc.get("args", "")
                if args_str:
                    lines.append(f"  Args: {args_str[:120]}")

        if memory_ops:
            lines.extend(["", "MEMORY OPERATIONS:"])
            for op in memory_ops:
                key = op.get("key", "?")
                value = op.get("value", "")[:80]
                category = op.get("category", "data_extraction")
                lines.append(f"  SAVE [{category}]: key=\"{key}\" value=\"{value}\"")

        if loop_warning:
            lines.extend(["", f"⚠ LOOP: {loop_warning[:140]}"])

        lines.extend(["", "META:"])
        token_str = f"{prompt_tokens:,} + {completion_tokens:,} = {prompt_tokens + completion_tokens:,}"
        cost_str = f"${step_cost:.4f}" if cost_known else "N/A"
        lines.append(f"  Tokens: {token_str} | Cost: {cost_str} | Duration: {step_duration_ms / 1000:.1f}s")
        cum_cost_str = f"${self.total_cost:.4f}" if cost_known else "N/A"
        lines.append(f"  Cumulative: {self.total_tokens:,} tokens | {cum_cost_str}")
        lines.append(_SEP)

        logger.info("\n".join(lines))

    # ------------------------------------------------------------------
    # Public: observation
    # ------------------------------------------------------------------

    def log_observation(
        self,
        *,
        step: int,
        url: str,
        title: str,
        element_count: int,
        fingerprint: str,
        screenshot_captured: bool,
    ) -> None:
        shot = "screenshot=yes" if screenshot_captured else "screenshot=no"
        logger.debug(
            f"OBSERVE step={step} elements={element_count} "
            f"fp={fingerprint[:12]}... {shot} | {title[:60]} | {url[:80]}"
        )

    # ------------------------------------------------------------------
    # Public: execution result
    # ------------------------------------------------------------------

    def log_execution_result(
        self,
        *,
        step: int,
        tool_name: str,
        success: bool,
        page_changed: bool,
        error: str = "",
        blocked_by_loop: bool = False,
    ) -> None:
        if blocked_by_loop:
            logger.warning(f"step={step} tool={tool_name} BLOCKED (repeated action on unchanged page)")
        elif not success:
            logger.warning(f"step={step} tool={tool_name} FAILED: {error[:120]}")
        else:
            changed = " page_changed=True" if page_changed else ""
            logger.debug(f"step={step} tool={tool_name} OK{changed}")

    # ------------------------------------------------------------------
    # Public: memory operation
    # ------------------------------------------------------------------

    def log_memory_operation(
        self,
        *,
        step: int,
        key: str,
        value: str,
        trigger_category: str,
    ) -> None:
        self.memory_save_count += 1
        truncated = value[:80] + ("..." if len(value) > 80 else "")
        logger.info(
            f"MEMORY [{trigger_category}] step={step} "
            f"key=\"{key}\" value=\"{truncated}\""
        )

    # ------------------------------------------------------------------
    # Public: phase transition warning
    # ------------------------------------------------------------------

    def log_phase_transition_warning(
        self,
        *,
        current_url: str,
        page_title: str,
        old_subtask: str,
        new_subtask: str,
        memory_saved_this_step: bool,
    ) -> None:
        if memory_saved_this_step:
            return
        text = (current_url + " " + page_title).lower()
        data_rich_kw = ("order", "cart", "checkout", "history", "product", "заказ", "корзин", "товар", "check")
        if any(kw in text for kw in data_rich_kw):
            logger.warning(
                f"PHASE SWITCH without memory save on data-rich page: "
                f"\"{old_subtask[:60]}\" -> \"{new_subtask[:60]}\" | URL: {current_url[:80]}"
            )

    # ------------------------------------------------------------------
    # Public: conversation history debug
    # ------------------------------------------------------------------

    def log_conversation_history(
        self,
        *,
        step: int,
        messages: list[dict[str, Any]],
    ) -> None:
        lines = [f"CONVERSATION HISTORY (step={step}, messages={len(messages)}):"]
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")

            if role == "system":
                lines.append(f"  [{i}] system: {len(str(content))} chars")
            elif role == "user":
                if isinstance(content, list):
                    has_img = any(p.get("type") == "image_url" for p in content if isinstance(p, dict))
                    text_chars = sum(
                        len(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
                    )
                    lines.append(f"  [{i}] user: text={text_chars}c{' + screenshot' if has_img else ''}")
                else:
                    lines.append(f"  [{i}] user: {len(str(content))} chars")
            elif role == "assistant":
                if isinstance(content, str):
                    try:
                        import json as _json
                        data = _json.loads(content)
                        actions = [a.get("tool_name", "?") for a in data.get("action", [])]
                        lines.append(f"  [{i}] assistant: actions=[{', '.join(actions)}] next_goal={data.get('next_goal','')[:40]!r}")
                    except Exception:
                        lines.append(f"  [{i}] assistant: {len(content)} chars")
            else:
                lines.append(f"  [{i}] {role}: ...")

        logger.debug("\n".join(lines))

    # ------------------------------------------------------------------
    # Public: final summary
    # ------------------------------------------------------------------

    def log_summary(self, *, status: str, steps: int) -> None:
        avg_dur = (
            f"{sum(self.step_durations) / len(self.step_durations) / 1000:.1f}s"
            if self.step_durations
            else "N/A"
        )
        _, cost_known = self._compute_cost(1, 1)  # check if model is known
        cost_str = f"${self.total_cost:.4f}" if cost_known else "N/A"

        lines = [
            _SEP,
            f"RUN SUMMARY | Status: {status.upper()} | Steps: {steps}",
            _SEP,
            f"  Tokens:         {self.total_prompt_tokens:,} prompt + {self.total_completion_tokens:,} completion = {self.total_tokens:,} total",
            f"  Cost:           {cost_str}",
            f"  Avg step:       {avg_dur}",
            f"  Memory saves:   {self.memory_save_count}",
            f"  Loop warnings:  {self.loop_warning_count}",
            _SEP,
        ]
        logger.info("\n".join(lines))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_plan_status(
        self,
        plan_value: str | None,
        current_subtask: str,
    ) -> list[str]:
        if not plan_value:
            return []

        # Split on numbered markers like "1)" "2." "Phase 1:" etc.
        parts = re.split(r"(?=\d+[).]\s)", plan_value.strip())
        items = [p.strip() for p in parts if p.strip()]
        if not items:
            return [f"Plan: {plan_value[:120]}"]

        # Find current phase by matching current_subtask against item text
        current_idx = -1
        if current_subtask:
            for i, item in enumerate(items):
                item_text = re.sub(r"^\d+[).]\s*", "", item).lower()
                sub_lower = current_subtask.lower()
                # Try direct substring match (first 30 chars of item text in subtask)
                if item_text and item_text[:30] in sub_lower:
                    current_idx = i
                    break
                # Fallback: significant words overlap
                words = [w for w in item_text.split()[:5] if len(w) > 4]
                if words and any(w in sub_lower for w in words):
                    current_idx = i
                    break

        result = []
        for i, item in enumerate(items):
            if current_idx == -1:
                marker = "[ ]"
            elif i < current_idx:
                marker = "[x]"
            elif i == current_idx:
                marker = "[>]"
            else:
                marker = "[ ]"
            result.append(f"{marker} {item[:80]}")
        return result

    def _compute_cost(self, prompt_tokens: int, completion_tokens: int) -> tuple[float, bool]:
        pricing = MODEL_PRICING.get(MAIN_MODEL)
        if pricing is None:
            return 0.0, False
        cost = prompt_tokens * pricing["prompt"] + completion_tokens * pricing["completion"]
        return cost, True


# ------------------------------------------------------------------
# Module-level helper
# ------------------------------------------------------------------

def _wrap(text: str, width: int, indent: str) -> list[str]:
    """Word-wrap text into lines of at most `width` chars with leading `indent`."""
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        sep = " " if current != indent else ""
        candidate = current + sep + word
        if len(candidate) > width and current != indent:
            lines.append(current)
            current = indent + word
        else:
            current = candidate
    if current != indent:
        lines.append(current)
    return lines or [indent]
