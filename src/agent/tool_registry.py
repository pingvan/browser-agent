from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

ToolCategory = Literal["browser", "state"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: ToolCategory
    parameters: dict[str, Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._specs = {
            spec.name: spec
            for spec in [
                ToolSpec(
                    name="navigate",
                    description="Open a full http or https URL in the current tab.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Full URL to open."},
                        },
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="click",
                    description="Click an interactive element by its element_id.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {
                            "element_id": {
                                "type": "integer",
                                "description": "Element reference from the current observation.",
                            }
                        },
                        "required": ["element_id"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="click_coordinates",
                    description=(
                        "Click viewport-relative coordinates when the screenshot is reliable "
                        "but element IDs are ambiguous or stale."
                    ),
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "description": {
                                "type": "string",
                                "description": "Short human-readable target description.",
                            },
                        },
                        "required": ["x", "y", "description"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="type_text",
                    description="Type text into an input-like element. Optionally press Enter after typing.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {
                            "element_id": {"type": "integer"},
                            "text": {"type": "string"},
                            "press_enter": {"type": "boolean"},
                        },
                        "required": ["element_id", "text"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="press_key",
                    description="Press a keyboard key such as Enter, Escape, ArrowDown, Tab.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {"key": {"type": "string"}},
                        "required": ["key"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="scroll",
                    description="Scroll the current page up or down by a pixel amount.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {
                            "direction": {"type": "string", "enum": ["up", "down"]},
                            "amount": {"type": "integer"},
                        },
                        "required": ["direction"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="go_back",
                    description="Go back in browser history.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="get_tabs",
                    description="Inspect currently open browser tabs.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="switch_tab",
                    description="Switch to an open browser tab by index.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {"index": {"type": "integer"}},
                        "required": ["index"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="wait",
                    description="Wait for a short number of seconds.",
                    category="browser",
                    parameters={
                        "type": "object",
                        "properties": {"seconds": {"type": "number"}},
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="save_memory",
                    description="Save a durable factual memory that will be useful on later pages.",
                    category="state",
                    parameters={
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["key", "value"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="ask_user",
                    description="Ask the user for missing information before continuing.",
                    category="state",
                    parameters={
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                        "required": ["question"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="done",
                    description="Finish the task and report the answer to the user.",
                    category="state",
                    parameters={
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"],
                        "additionalProperties": False,
                    },
                ),
            ]
        }

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]

    def is_browser_action(self, name: str) -> bool:
        spec = self._specs.get(name)
        return spec is not None and spec.category == "browser"

    def is_known(self, name: str) -> bool:
        return name in self._specs

    def validate(self, name: str, arguments: dict[str, Any]) -> str | None:
        if name not in self._specs:
            return f"Unknown tool: {name}"

        required = set(self._specs[name].parameters.get("required", []))
        missing = [field for field in required if field not in arguments]
        if missing:
            return f"Missing required arguments for {name}: {', '.join(sorted(missing))}"

        if name in {"click", "switch_tab"}:
            target = "element_id" if name == "click" else "index"
            if not isinstance(arguments.get(target), int):
                return f"{name}.{target} must be an integer"

        if name == "click_coordinates":
            if not isinstance(arguments.get("x"), int):
                return "click_coordinates.x must be an integer"
            if not isinstance(arguments.get("y"), int):
                return "click_coordinates.y must be an integer"
            if not isinstance(arguments.get("description"), str):
                return "click_coordinates.description must be a string"

        if name == "type_text":
            if not isinstance(arguments.get("element_id"), int):
                return "type_text.element_id must be an integer"
            if not isinstance(arguments.get("text"), str):
                return "type_text.text must be a string"

        if name == "navigate":
            url = str(arguments.get("url", ""))
            if not url.startswith(("http://", "https://")):
                return "navigate.url must start with http:// or https://"

        if name == "scroll":
            direction = arguments.get("direction")
            if direction not in {"up", "down"}:
                return "scroll.direction must be 'up' or 'down'"
            if "amount" in arguments:
                try:
                    int(arguments["amount"])
                except (TypeError, ValueError):
                    return "scroll.amount must be an integer"

        if name == "wait" and "seconds" in arguments:
            try:
                float(arguments["seconds"])
            except (TypeError, ValueError):
                return "wait.seconds must be numeric"

        return None

    def parse_arguments(self, raw_arguments: str) -> dict[str, Any]:
        if not raw_arguments.strip():
            return {}
        parsed = json.loads(raw_arguments)
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must decode to a JSON object")
        return parsed

    def render_action_signature(self, name: str, arguments: dict[str, Any]) -> str:
        compact = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        return f"{name}:{compact}"
