from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aioconsole

from src.security.classifier import SecurityClassifier
from src.security.schema import RiskLevel, SecurityVerdict
from src.utils.logger import logger

ALWAYS_SAFE_ACTIONS = frozenset(
    {
        "scroll",
        "wait",
        "go_back",
        "navigate",
        "get_tabs",
        "switch_tab",
        "press_key",
        "save_memory",
        "inspect_dom",
        "step_meta",
        "set_subtask",
        "done",
        "ask_user",
    }
)

NEEDS_CLASSIFICATION = frozenset({"click", "type_text"})


def needs_security_check(action_name: str, arguments: dict[str, Any]) -> bool:
    del arguments
    if action_name in ALWAYS_SAFE_ACTIONS:
        return False
    return action_name in NEEDS_CLASSIFICATION


class SecurityGate:
    """Pre-execution security gate using an LLM classifier."""

    def __init__(self, classifier: SecurityClassifier) -> None:
        self.classifier = classifier
        self.audit_log: list[dict[str, Any]] = []

    async def check(
        self,
        *,
        action_name: str,
        arguments: dict[str, Any],
        element_info: Mapping[str, Any] | None,
        page_url: str,
        page_title: str,
        page_text_excerpt: str,
        prompt_injection_warnings: list[str] | None,
        user_task: str,
        screenshot_b64: str = "",
        step: int = 0,
    ) -> tuple[bool, SecurityVerdict | None]:
        if not needs_security_check(action_name, arguments):
            return True, None

        verdict = await self.classifier.classify(
            action_name=action_name,
            arguments=arguments,
            element_info=element_info,
            page_url=page_url,
            page_title=page_title,
            page_text_excerpt=page_text_excerpt,
            prompt_injection_warnings=prompt_injection_warnings or [],
            user_task=user_task,
            screenshot_b64=screenshot_b64,
        )

        self.audit_log.append(
            {
                "step": step,
                "action": action_name,
                "risk": verdict.risk_level.value,
                "category": verdict.category,
                "needs_confirmation": verdict.needs_confirmation,
                "reason": verdict.reason,
            }
        )

        self._log_verdict(action_name, verdict)

        if not verdict.needs_confirmation:
            return True, verdict

        allowed = await self._ask_confirmation(verdict, action_name, step)
        self.audit_log[-1]["user_decision"] = "approved" if allowed else "denied"
        return allowed, verdict

    async def _ask_confirmation(
        self, verdict: SecurityVerdict, action_name: str, step: int
    ) -> bool:
        risk_label = verdict.risk_level.value.upper()
        border = "=" * 64

        print(f"\n{border}")
        print(f"  [{risk_label} RISK] Confirmation Required  (step {step})")
        print(border)
        print(f"  {verdict.user_facing_message or 'The agent wants to perform a sensitive action. Allow?'}")
        print(f"\n  Category: {verdict.category}")
        print(f"  Reason: {verdict.reason}")
        print(border)

        try:
            answer = (await aioconsole.ainput("  Allow? [y/N]: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        allowed = answer in ("y", "yes")

        if allowed:
            logger.info(f"SECURITY: User APPROVED {action_name}")
        else:
            logger.info(f"SECURITY: User DENIED {action_name}")

        print(border + "\n")
        return allowed

    def _log_verdict(self, action_name: str, verdict: SecurityVerdict) -> None:
        if verdict.risk_level == RiskLevel.CRITICAL:
            logger.warning(f"SECURITY [CRITICAL]: {action_name} -- {verdict.reason}")
            return
        if verdict.risk_level == RiskLevel.HIGH:
            logger.warning(f"SECURITY [HIGH]: {action_name} -- {verdict.reason}")
            return
        if verdict.risk_level == RiskLevel.MODERATE:
            logger.info(f"SECURITY [moderate]: {action_name} -- {verdict.reason}")
            return
        logger.debug(f"SECURITY [safe]: {action_name} -- {verdict.reason}")

    def get_summary(self) -> str:
        if not self.audit_log:
            return "Security: no actions classified"

        total = len(self.audit_log)
        by_risk: dict[str, int] = {}
        for entry in self.audit_log:
            risk = str(entry["risk"])
            by_risk[risk] = by_risk.get(risk, 0) + 1

        confirmations = sum(1 for entry in self.audit_log if entry["needs_confirmation"])
        denied = sum(1 for entry in self.audit_log if entry.get("user_decision") == "denied")

        return (
            f"Security: {total} actions classified. "
            f"Risk distribution: {by_risk}. "
            f"Confirmations requested: {confirmations}. "
            f"User denied: {denied}."
        )
