"""message_builder — factory functions for all agent messages.

Every function returns a TaggedMessage: a thin wrapper that carries
a semantic tag (used by ContextManager for pinning/pruning decisions)
alongside the raw OpenAI-compatible dict that is sent to the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from src.agent.plan_tracker import PlanTracker

MessageTag = Literal[
    "system_prompt",
    "task",
    "task_reminder",
    "plan",
    "tool_result",
    "assistant",
    "user_prompt",
    "context_note",
]


@dataclass
class TaggedMessage:
    tag: MessageTag
    msg: dict[str, Any]
    has_screenshot: bool = field(default=False)


# ---------------------------------------------------------------------------
# Pinned messages (always kept by ContextManager)
# ---------------------------------------------------------------------------


def build_system_message(prompt: str) -> TaggedMessage:
    return TaggedMessage(tag="system_prompt", msg={"role": "system", "content": prompt})


def build_task_message(task: str) -> TaggedMessage:
    return TaggedMessage(tag="task", msg={"role": "user", "content": task})


# ---------------------------------------------------------------------------
# Dynamic pinned / injected messages
# ---------------------------------------------------------------------------


def build_plan_message(plan: PlanTracker, step: int) -> TaggedMessage:
    return TaggedMessage(tag="plan", msg={"role": "user", "content": plan.render(step)})


def build_task_reminder(task: str, step: int) -> TaggedMessage:
    content = f"[Task reminder — step {step}] Your current task: {task}"
    return TaggedMessage(tag="task_reminder", msg={"role": "user", "content": content})


def build_context_note(removed: int) -> TaggedMessage:
    content = (
        f"[Context note: {removed} earlier messages were trimmed. "
        "The task and recent actions are preserved.]"
    )
    return TaggedMessage(tag="context_note", msg={"role": "user", "content": content})


# ---------------------------------------------------------------------------
# History messages
# ---------------------------------------------------------------------------


def build_assistant_message(msg_dict: dict[str, Any]) -> TaggedMessage:
    return TaggedMessage(tag="assistant", msg=msg_dict)


def build_action_result(
    text_content: str,
    *,
    screenshot_b64: str | None = None,
) -> TaggedMessage:
    """Build a user-role message containing action chain results.

    With structured JSON output (no tool calling), results are injected as
    user messages rather than tool-role messages.
    """
    has_screenshot = bool(screenshot_b64)

    if not has_screenshot:
        msg: dict[str, Any] = {
            "role": "user",
            "content": text_content,
        }
    else:
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": text_content},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_b64}",
                        "detail": "low",
                    },
                },
            ],
        }

    return TaggedMessage(tag="tool_result", msg=msg, has_screenshot=has_screenshot)


def build_budget_warning(step: int, max_steps: int) -> TaggedMessage:
    remaining = max_steps - step
    if step >= max_steps - 1:
        content = (
            f"[CRITICAL: Step {step}/{max_steps} — This is your LAST step. "
            "You MUST call 'done' now with whatever you have accomplished so far.]"
        )
    else:
        content = (
            f"[Budget warning: Step {step}/{max_steps} — "
            f"Only {remaining} steps remaining. Start wrapping up and consider calling 'done' soon.]"
        )
    return TaggedMessage(tag="task_reminder", msg={"role": "user", "content": content})


def build_json_error(raw: str, error: str) -> TaggedMessage:
    """Build an error message when the model's JSON response is malformed."""
    content = (
        f"[ERROR] Your previous response could not be parsed: {error}\n\n"
        "Please respond with valid JSON matching the format described in Section 9 "
        "of your instructions. Your response must contain at minimum:\n"
        '{"thinking": "...", "action": [{"tool_name": {"arg": "value"}}]}\n\n'
        f"Raw response (first 500 chars): {raw[:500]}"
    )
    return TaggedMessage(tag="user_prompt", msg={"role": "user", "content": content})
