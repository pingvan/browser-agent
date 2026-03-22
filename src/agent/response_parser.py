"""response_parser — parse and validate structured JSON from the model.

The agent is expected to respond with JSON matching Section 7 of the system
prompt.  This module extracts the fields into a typed dataclass and provides
clear error messages when the response is malformed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResponse:
    """Parsed structured response from the model."""

    thinking: str = ""
    evaluation_previous_goal: str = ""
    memory: str = ""
    next_goal: str = ""
    current_plan_item: int | None = None
    plan_update: list[str] | None = None
    action: list[dict[str, Any]] = field(default_factory=list)

    # Raw content for logging / assistant message storage.
    raw: str = ""


class ParseError(Exception):
    """Raised when the model response cannot be parsed."""

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


def parse_response(raw_content: str) -> AgentResponse:
    """Parse the model's JSON content into an ``AgentResponse``.

    Raises ``ParseError`` with a descriptive message on failure.
    """
    if not raw_content or not raw_content.strip():
        raise ParseError("Model returned empty content.", raw=raw_content or "")

    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Invalid JSON: {exc}. Make sure your entire response is valid JSON.",
            raw=raw_content,
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            f"Expected a JSON object, got {type(data).__name__}.",
            raw=raw_content,
        )

    action_raw = data.get("action", [])
    if not isinstance(action_raw, list):
        raise ParseError(
            'The \'action\' field must be a JSON array, e.g. [{"navigate": {"url": "..."}}].',
            raw=raw_content,
        )

    # Validate each action is a single-key dict {tool_name: args_dict}
    actions: list[dict[str, Any]] = []
    for i, item in enumerate(action_raw):
        if not isinstance(item, dict) or len(item) != 1:
            raise ParseError(
                f"action[{i}] must be a single-key object like "
                f'{{"tool_name": {{...}}}}, got: {json.dumps(item, ensure_ascii=False)}',
                raw=raw_content,
            )
        actions.append(item)

    plan_update = data.get("plan_update")
    if plan_update is not None and not isinstance(plan_update, list):
        plan_update = None

    current_plan_item = data.get("current_plan_item")
    if current_plan_item is not None:
        try:
            current_plan_item = int(current_plan_item)
        except (TypeError, ValueError):
            current_plan_item = None

    return AgentResponse(
        thinking=str(data.get("thinking", "")),
        evaluation_previous_goal=str(data.get("evaluation_previous_goal", "")),
        memory=str(data.get("memory", "")),
        next_goal=str(data.get("next_goal", "")),
        current_plan_item=current_plan_item,
        plan_update=plan_update,
        action=actions,
        raw=raw_content,
    )


def format_parse_error(raw: str, error: Exception) -> str:
    """Produce an error message to send back to the model so it can recover."""
    return (
        f"[ERROR] Your previous response could not be parsed: {error}\n\n"
        "Please respond with valid JSON matching the format described in Section 7 "
        "of your instructions. Your response must contain at minimum:\n"
        '{"thinking": "...", "action": [{"tool_name": {"arg": "value"}}]}\n\n'
        f"Raw response (first 500 chars): {raw[:500]}"
    )
