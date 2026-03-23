from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    tool_name: str
    arguments: dict[str, Any]


class AgentOutput(BaseModel):
    evaluation_previous_goal: str
    memory: str
    next_goal: str
    action: list[AgentAction] = Field(min_length=1)

    @classmethod
    def to_json_schema(cls) -> dict:
        return {
            "name": "agent_output",
            "strict": False,
            "schema": {
                "type": "object",
                "properties": {
                    "evaluation_previous_goal": {"type": "string"},
                    "memory": {"type": "string"},
                    "next_goal": {"type": "string"},
                    "action": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool_name": {"type": "string"},
                                "arguments": {"type": "object"},
                            },
                            "required": ["tool_name", "arguments"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                    },
                },
                "required": ["evaluation_previous_goal", "memory", "next_goal", "action"],
                "additionalProperties": False,
            },
        }
