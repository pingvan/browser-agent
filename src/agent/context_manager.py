from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.message_builder import (
    TaggedMessage,
    build_budget_warning,
    build_context_note,
    build_plan_message,
    build_task_reminder,
)

if TYPE_CHECKING:
    from src.agent.plan_tracker import PlanTracker

MAX_MESSAGES = 40
TASK_REMINDER_INTERVAL = 10

_PINNED_TAGS: frozenset[str] = frozenset({"system_prompt", "task"})


class ContextManager:
    MAX_SCREENSHOTS_KEPT = 1

    def _strip_screenshot(self, msg: TaggedMessage) -> TaggedMessage:
        content = msg.msg.get("content")
        if not isinstance(content, list):
            return msg

        stripped: list[Any] = []
        replaced = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                stripped.append(
                    {
                        "type": "text",
                        "text": "[Screenshot removed — outdated. Refer to the latest screenshot above.]",
                    }
                )
                replaced = True
                continue
            stripped.append(part)

        if not replaced:
            return msg

        return TaggedMessage(tag=msg.tag, msg={**msg.msg, "content": stripped})

    def _manage_screenshots(self, body: list[TaggedMessage]) -> list[TaggedMessage]:
        screenshot_indices = [i for i, m in enumerate(body) if m.has_screenshot]
        if len(screenshot_indices) <= self.MAX_SCREENSHOTS_KEPT:
            return body
        to_strip = set(screenshot_indices[: -self.MAX_SCREENSHOTS_KEPT])
        return [self._strip_screenshot(m) if i in to_strip else m for i, m in enumerate(body)]

    def prepare(
        self,
        messages: list[TaggedMessage],
        *,
        task: str,
        step: int,
        max_steps: int = 50,
        plan: PlanTracker | None = None,
    ) -> list[dict[str, Any]]:
        pinned = [m for m in messages if m.tag in _PINNED_TAGS]
        body = [m for m in messages if m.tag not in _PINNED_TAGS]

        body = self._manage_screenshots(body)

        removed = 0
        if len(body) > MAX_MESSAGES:
            removed = len(body) - MAX_MESSAGES
            body = [build_context_note(removed)] + body[-MAX_MESSAGES:]

        result = list(pinned)

        if plan is not None:
            result.append(build_plan_message(plan, step))

        result += body

        if step >= max_steps - 1 or step >= int(0.75 * max_steps):
            result.append(build_budget_warning(step, max_steps))
        elif step % TASK_REMINDER_INTERVAL == 0 or removed > 0:
            result.append(build_task_reminder(task, step))

        return [m.msg for m in result]
