from __future__ import annotations

from enum import StrEnum

from openai.types.shared_params.response_format_json_schema import JSONSchema
from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(StrEnum):
    SAFE = "safe"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class SecurityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel = Field(description="Risk classification of the proposed action")
    needs_confirmation: bool = Field(
        description="Whether user confirmation is required before executing"
    )
    category: str = Field(
        description=(
            "Action category: 'navigation', 'data_input', 'form_submit', "
            "'purchase', 'authentication', 'deletion', 'communication', "
            "'settings_change', 'content_publish', 'benign_interaction'"
        )
    )
    reason: str = Field(description="One sentence explanation of the classification decision")
    user_facing_message: str = Field(
        description=(
            "If needs_confirmation is true: a clear, non-technical message "
            "to show the user asking for permission. "
            "If needs_confirmation is false: empty string."
        )
    )

    @classmethod
    def to_json_schema(cls) -> JSONSchema:
        return {
            "name": "security_verdict",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "risk_level": {
                        "type": "string",
                        "enum": [level.value for level in RiskLevel],
                        "description": "Risk classification of the proposed action",
                    },
                    "needs_confirmation": {
                        "type": "boolean",
                        "description": "Whether user confirmation is required before executing",
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Action category: 'navigation', 'data_input', 'form_submit', "
                            "'purchase', 'authentication', 'deletion', 'communication', "
                            "'settings_change', 'content_publish', 'benign_interaction'"
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence explanation of the classification decision",
                    },
                    "user_facing_message": {
                        "type": "string",
                        "description": (
                            "If needs_confirmation is true: a clear, non-technical message "
                            "to show the user asking for permission. "
                            "If needs_confirmation is false: empty string."
                        ),
                    },
                },
                "required": [
                    "risk_level",
                    "needs_confirmation",
                    "category",
                    "reason",
                    "user_facing_message",
                ],
                "additionalProperties": False,
            },
        }
