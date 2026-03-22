from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from openai import AsyncOpenAI

from src.agent.prompts import PLANNER_SYSTEM_PROMPT
from src.agent.state import MemoryEntry, PlanStep, normalize_plan
from src.config.settings import PLAN_MODEL, TEMPERATURE
from src.utils.logger import logger


class Planner:
    def __init__(self, client: AsyncOpenAI | None) -> None:
        self._client = client

    async def build_plan(
        self,
        *,
        task: str,
        memory: list[MemoryEntry],
        existing_plan: list[PlanStep] | None = None,
        replan_reason: str = "",
        action_history: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[list[PlanStep], str]:
        logger.debug(
            f"Planner.build_plan: task={task[:160]!r}, memory_entries={len(memory)}, "
            f"existing_plan_steps={len(existing_plan or [])}, replan_reason={replan_reason[:160]!r}"
        )
        if self._client is None:
            logger.debug("Planner.build_plan: no client, using fallback plan")
            return normalize_plan(self._fallback_steps(task, existing_plan)), self._fallback_reason(
                replan_reason
            )

        payload = {
            "task": task,
            "memory": memory,
            "existing_plan": existing_plan or [],
            "replan_reason": replan_reason,
            "action_history": action_history or [],
        }

        try:
            response = await self._client.chat.completions.create(
                model=PLAN_MODEL,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
            )
        except Exception as exc:
            logger.warning(f"Planner.build_plan: model call failed, using fallback plan: {exc}")
            return normalize_plan(self._fallback_steps(task, existing_plan)), self._fallback_reason(
                replan_reason
            )

        raw = response.choices[0].message.content or "{}"
        logger.debug(f"Planner.build_plan raw response: {raw[:1200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"Planner.build_plan: invalid JSON, using fallback plan: {exc}")
            return normalize_plan(self._fallback_steps(task, existing_plan)), self._fallback_reason(
                replan_reason
            )

        steps_raw = data.get("steps", [])
        reasoning = str(data.get("reasoning", "")).strip() or self._fallback_reason(replan_reason)
        if not isinstance(steps_raw, list):
            steps_raw = []

        steps = [str(step).strip() for step in steps_raw if str(step).strip()]
        if not steps:
            logger.warning("Planner.build_plan: model returned empty steps, using fallback plan")
            steps = self._fallback_steps(task, existing_plan)

        logger.debug(f"Planner.build_plan final steps: {steps}")
        return normalize_plan(steps), reasoning

    def _fallback_steps(self, task: str, existing_plan: list[PlanStep] | None) -> list[str]:
        completed = [
            step["description"]
            for step in (existing_plan or [])
            if step.get("status") == "done" and step.get("description")
        ]

        generated = [
            "Понять, на каком сайте и в каком разделе нужно выполнять задачу",
            "Найти страницу или интерфейс, где можно выполнить запрос пользователя",
            "Собрать важные факты и сохранить их в память",
            "Выполнить основное действие по задаче",
            "Проверить результат и подготовить отчёт пользователю",
        ]

        if "оплат" in task.lower() or "закаж" in task.lower() or "куп" in task.lower():
            generated[-1] = "Остановиться перед оплатой или подтверждением и спросить пользователя"

        remaining = [step for step in generated if step not in completed]
        return completed + remaining

    def _fallback_reason(self, replan_reason: str) -> str:
        if replan_reason:
            return f"План обновлён из-за изменения ситуации: {replan_reason}"
        return "План построен эвристически, чтобы разбить задачу на наблюдаемые браузерные шаги."
