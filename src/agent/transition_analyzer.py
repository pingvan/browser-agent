from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypedDict

from openai import AsyncOpenAI

from src.agent.state import AgentState, ElementSnapshot, MemoryEntry, PlanStep
from src.config.settings import SUMMARY_MODEL, TEMPERATURE
from src.utils.logger import logger

TRANSITION_ANALYZER_PROMPT = """
Ты анализируешь переход состояния браузерного агента между двумя наблюдениями.

Твоя задача:
- определить, произошла ли значимая смена состояния страницы;
- определить, есть ли реальный прогресс по текущему шагу плана;
- определить, можно ли автоматически отметить текущий шаг плана как завершённый;
- определить, выглядит ли вся пользовательская задача уже выполненной;
- предложить 0-3 записей в рабочую память, только если это долговечные факты, которые будут полезны после смены страницы.

Правила:
- Будь универсальным: не опирайся на знания о конкретном сайте.
- Считай прогрессом достижение нового значимого состояния, а не просто успешный клик.
- Отмечай complete_current_step=true только если из after-state ясно, что текущий шаг реально достигнут.
- Отмечай task_completed=true только если из after-state ясно, что пользовательская цель уже достигнута.
- В memory_updates сохраняй только факты вида "что найдено", "что выбрано", "что подтверждено", "какой результат достигнут".
- Не сохраняй временные UI-детали, которые потеряют смысл на следующей странице.

Ответь строго JSON:
{
  "reasoning": "кратко",
  "significant_change": true,
  "progress_made": true,
  "complete_current_step": false,
  "step_result": "",
  "task_completed": false,
  "final_report": "",
  "memory_updates": [
    {"key": "...", "value": "..."}
  ]
}
""".strip()

STATE_EVALUATOR_PROMPT = """
Ты оцениваешь текущее состояние страницы браузерного агента.

Твоя задача:
- определить, достигнут ли текущий шаг плана уже сейчас;
- определить, выглядит ли вся пользовательская задача уже выполненной;
- определить, есть ли заметный прогресс относительно текущего шага;
- предложить 0-3 записей в рабочую память, только если это устойчивые факты, которые пригодятся позже.

Правила:
- Будь универсальным: не опирайся на знания о конкретном сайте.
- Считай шаг выполненным, если текущее состояние явно подтверждает цель шага, даже если агент пришёл сюда не тем путём, который ожидался в плане.
- Считай задачу выполненной, если по текущему состоянию страницы видно, что цель пользователя уже достигнута, даже если некоторые промежуточные шаги плана формально не отмечены.
- В memory_updates сохраняй только долговечные факты: найденные сущности, выбранные значения, подтверждённые результаты, важные идентификаторы или ссылки.
- Не сохраняй шум интерфейса и временные декоративные тексты.

Ответь строго JSON:
{
  "reasoning": "кратко",
  "progress_made": true,
  "complete_current_step": false,
  "step_result": "",
  "task_completed": false,
  "final_report": "",
  "memory_updates": [
    {"key": "...", "value": "..."}
  ]
}
""".strip()


class TransitionMemory(TypedDict):
    key: str
    value: str


class TransitionAnalysis(TypedDict):
    reasoning: str
    significant_change: bool
    progress_made: bool
    complete_current_step: bool
    step_result: str
    task_completed: bool
    final_report: str
    memory_updates: list[TransitionMemory]


class StateEvaluation(TypedDict):
    reasoning: str
    progress_made: bool
    complete_current_step: bool
    step_result: str
    task_completed: bool
    final_report: str
    memory_updates: list[TransitionMemory]


@dataclass
class TransitionAnalyzer:
    client: AsyncOpenAI | None

    async def analyze_transition(
        self,
        *,
        task: str,
        plan: list[PlanStep],
        current_plan_step: int,
        last_action: dict[str, Any] | None,
        last_action_result: dict[str, Any] | None,
        before_state: AgentState,
        after_state: AgentState,
        allow_llm: bool = True,
    ) -> TransitionAnalysis:
        heuristic = self._heuristic_analysis(
            task=task,
            plan=plan,
            current_plan_step=current_plan_step,
            last_action=last_action,
            last_action_result=last_action_result,
            before_state=before_state,
            after_state=after_state,
        )
        if last_action is None or self.client is None or not allow_llm:
            return heuristic

        current_step_description = ""
        if 0 <= current_plan_step < len(plan):
            current_step_description = plan[current_plan_step]["description"]

        payload = {
            "task": task,
            "current_plan_step": current_plan_step,
            "current_step_description": current_step_description,
            "last_action": last_action,
            "last_action_result": last_action_result or {},
            "before": self._serialize_state(before_state),
            "after": self._serialize_state(after_state),
        }

        try:
            response = await self.client.chat.completions.create(
                model=SUMMARY_MODEL,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": TRANSITION_ANALYZER_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
        except Exception as exc:
            logger.warning(f"TransitionAnalyzer: model call failed, using heuristic analysis: {exc}")
            return heuristic

        raw = response.choices[0].message.content or "{}"
        logger.debug(f"TransitionAnalyzer raw response: {raw[:1200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"TransitionAnalyzer: invalid JSON, using heuristic analysis: {exc}")
            return heuristic

        memory_updates_raw = data.get("memory_updates", [])
        if not isinstance(memory_updates_raw, list):
            memory_updates_raw = []

        memory_updates: list[TransitionMemory] = []
        for item in memory_updates_raw[:3]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
            if key and value:
                memory_updates.append(TransitionMemory(key=key, value=value))

        return TransitionAnalysis(
            reasoning=str(data.get("reasoning", "")).strip(),
            significant_change=bool(data.get("significant_change", False)),
            progress_made=bool(data.get("progress_made", False)),
            complete_current_step=bool(data.get("complete_current_step", False)),
            step_result=str(data.get("step_result", "")).strip(),
            task_completed=bool(data.get("task_completed", False)),
            final_report=str(data.get("final_report", "")).strip(),
            memory_updates=memory_updates,
        )

    async def evaluate_current_state(
        self,
        *,
        task: str,
        plan: list[PlanStep],
        current_plan_step: int,
        memory: list[MemoryEntry],
        state: AgentState,
        allow_llm: bool = True,
    ) -> StateEvaluation:
        heuristic = self._heuristic_state_evaluation(
            task=task,
            plan=plan,
            current_plan_step=current_plan_step,
            memory=memory,
            state=state,
        )
        if self.client is None or not allow_llm:
            return heuristic

        current_step_description = ""
        if 0 <= current_plan_step < len(plan):
            current_step_description = plan[current_plan_step]["description"]

        payload = {
            "task": task,
            "current_plan_step": current_plan_step,
            "current_step_description": current_step_description,
            "memory": memory,
            "state": self._serialize_state(state),
        }

        try:
            response = await self.client.chat.completions.create(
                model=SUMMARY_MODEL,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": STATE_EVALUATOR_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
        except Exception as exc:
            logger.warning(f"TransitionAnalyzer: state evaluation failed, using heuristic: {exc}")
            return heuristic

        raw = response.choices[0].message.content or "{}"
        logger.debug(f"StateEvaluator raw response: {raw[:1200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"TransitionAnalyzer: invalid state evaluation JSON, using heuristic: {exc}")
            return heuristic

        return StateEvaluation(
            reasoning=str(data.get("reasoning", "")).strip(),
            progress_made=bool(data.get("progress_made", False)),
            complete_current_step=bool(data.get("complete_current_step", False)),
            step_result=str(data.get("step_result", "")).strip(),
            task_completed=bool(data.get("task_completed", False)),
            final_report=str(data.get("final_report", "")).strip(),
            memory_updates=self._parse_memory_updates(data.get("memory_updates", [])),
        )

    def _serialize_state(self, state: AgentState) -> dict[str, Any]:
        return {
            "url": state.get("current_url", ""),
            "title": state.get("page_title", ""),
            "summary": state.get("page_summary", ""),
            "elements": self._serialize_elements(state.get("interactive_elements", [])),
        }

    def _serialize_elements(self, elements: list[ElementSnapshot]) -> list[dict[str, Any]]:
        serialized = []
        for element in elements[:20]:
            serialized.append(
                {
                    "id": element.get("index", element.get("ref")),
                    "tag": element.get("tag"),
                    "role": element.get("role"),
                    "text": element.get("text"),
                    "aria_label": element.get("aria_label"),
                    "placeholder": element.get("placeholder"),
                }
            )
        return serialized

    def _heuristic_analysis(
        self,
        *,
        task: str,
        plan: list[PlanStep],
        current_plan_step: int,
        last_action: dict[str, Any] | None,
        last_action_result: dict[str, Any] | None,
        before_state: AgentState,
        after_state: AgentState,
    ) -> TransitionAnalysis:
        before_url = str(before_state.get("current_url", ""))
        after_url = str(after_state.get("current_url", ""))
        before_title = str(before_state.get("page_title", ""))
        after_title = str(after_state.get("page_title", ""))
        before_summary = str(before_state.get("page_summary", ""))
        after_summary = str(after_state.get("page_summary", ""))
        before_elements = len(before_state.get("interactive_elements", []))
        after_elements = len(after_state.get("interactive_elements", []))
        before_state_text = self._state_text(before_state)
        after_state_text = self._state_text(after_state)

        before_fingerprint = str(before_state.get("page_fingerprint", "")).strip()
        after_fingerprint = str(after_state.get("page_fingerprint", "")).strip()
        significant_change = bool(last_action_result.get("page_changed", False)) if last_action_result else False
        if not significant_change:
            significant_change = (
                (before_fingerprint and after_fingerprint and before_fingerprint != after_fingerprint)
                or before_url != after_url
                or before_title != after_title
                or before_summary != after_summary
                or before_elements != after_elements
            )

        current_step_description = ""
        if 0 <= current_plan_step < len(plan):
            current_step_description = plan[current_plan_step]["description"]

        step_score_before = (
            self._score_text_match(current_step_description, before_state_text)
            if current_step_description
            else 0
        )
        step_score_after = (
            self._score_text_match(current_step_description, after_state_text)
            if current_step_description
            else 0
        )
        task_score_before = self._score_text_match(task, before_state_text) if task.strip() else 0
        task_score_after = self._score_text_match(task, after_state_text) if task.strip() else 0
        step_satisfied = (
            self._is_step_satisfied(current_step_description, after_state)
            if current_step_description
            else False
        )
        task_completed = self._is_task_satisfied(task, after_state)

        progress_made = False
        step_result = ""
        if significant_change:
            progress_made = bool(
                step_satisfied
                or task_completed
                or step_score_after > step_score_before
                or task_score_after > task_score_before
            )
            if step_satisfied or task_completed:
                step_result = self._summarize_state_fact(after_state)

        if not significant_change:
            reasoning = "Heuristic analysis: the page fingerprint did not change in a meaningful way."
        else:
            reasoning = "Heuristic analysis based on before/after state comparison."
        if current_step_description:
            reasoning += f" Current step: {current_step_description}"
            reasoning += f" Step relevance score: {step_score_before}->{step_score_after}."
        if task.strip():
            reasoning += f" Task relevance score: {task_score_before}->{task_score_after}."
        if significant_change and not progress_made:
            reasoning += " The new state changed, but it does not look closer to the current step or the task."

        return TransitionAnalysis(
            reasoning=reasoning,
            significant_change=significant_change,
            progress_made=progress_made,
            complete_current_step=step_satisfied,
            step_result=step_result,
            task_completed=task_completed,
            final_report=self._summarize_state_fact(after_state) if task_completed else "",
            memory_updates=[],
        )

    def _heuristic_state_evaluation(
        self,
        *,
        task: str,
        plan: list[PlanStep],
        current_plan_step: int,
        memory: list[MemoryEntry],
        state: AgentState,
    ) -> StateEvaluation:
        current_step_description = ""
        if 0 <= current_plan_step < len(plan):
            current_step_description = plan[current_plan_step]["description"]

        step_satisfied = self._is_step_satisfied(current_step_description, state)
        task_completed = self._is_task_satisfied(task, state)
        progress_made = step_satisfied or task_completed

        memory_updates: list[TransitionMemory] = []
        if task_completed:
            memory_updates.append(
                TransitionMemory(
                    key="verified_result",
                    value=self._summarize_state_fact(state),
                )
            )
        elif step_satisfied and current_step_description:
            memory_updates.append(
                TransitionMemory(
                    key="current_step_result",
                    value=current_step_description,
                )
            )

        existing_memory_keys = {str(item.get("key", "")) for item in memory}
        memory_updates = [
            item for item in memory_updates if item["key"] and item["key"] not in existing_memory_keys
        ][:3]

        reasoning_parts = ["Heuristic state evaluation based on page URL, title, summary, and visible elements."]
        if current_step_description:
            reasoning_parts.append(
                "Current step appears satisfied."
                if step_satisfied
                else "Current step is not clearly satisfied yet."
            )
        if task_completed:
            reasoning_parts.append("Current page appears to satisfy the user task.")

        final_report = ""
        if task_completed:
            final_report = self._summarize_state_fact(state)

        return StateEvaluation(
            reasoning=" ".join(reasoning_parts),
            progress_made=progress_made,
            complete_current_step=step_satisfied,
            step_result=self._summarize_state_fact(state) if step_satisfied else "",
            task_completed=task_completed,
            final_report=final_report,
            memory_updates=memory_updates,
        )

    def _parse_memory_updates(self, memory_updates_raw: Any) -> list[TransitionMemory]:
        if not isinstance(memory_updates_raw, list):
            memory_updates_raw = []

        memory_updates: list[TransitionMemory] = []
        for item in memory_updates_raw[:3]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
            if key and value:
                memory_updates.append(TransitionMemory(key=key, value=value))
        return memory_updates

    def _is_step_satisfied(self, step_description: str, state: AgentState) -> bool:
        if not step_description.strip():
            return False
        step_score = self._score_text_match(step_description, self._state_text(state))
        return step_score >= 2

    def _is_task_satisfied(self, task: str, state: AgentState) -> bool:
        if not task.strip():
            return False
        state_text = self._state_text(state)
        task_score = self._score_text_match(task, state_text)
        completion_score = self._score_text_match(
            "выполнено готово добавлено сохранено подтверждено открыто завершено",
            state_text,
        )
        return task_score >= 2 and completion_score >= 1

    def _state_text(self, state: AgentState) -> str:
        parts = [
            str(state.get("current_url", "")),
            str(state.get("page_title", "")),
            str(state.get("page_summary", "")),
        ]
        for element in state.get("interactive_elements", [])[:15]:
            parts.append(str(element.get("text", "")))
            parts.append(str(element.get("aria_label", "")))
            parts.append(str(element.get("placeholder", "")))
        return " ".join(part for part in parts if part).lower()

    def _score_text_match(self, text: str, state_text: str) -> int:
        roots = self._token_roots(text)
        if not roots or not state_text:
            return 0
        return sum(1 for root in roots if root in state_text)

    def _token_roots(self, text: str) -> set[str]:
        tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{4,}", text.lower())
        return {token[:5] for token in tokens}

    def _summarize_state_fact(self, state: AgentState) -> str:
        title = str(state.get("page_title", "")).strip()
        url = str(state.get("current_url", "")).strip()
        summary = str(state.get("page_summary", "")).strip()

        if title and summary:
            return f"{title}: {summary[:160]}"
        if title:
            return title
        if summary:
            return summary[:160]
        return url or "Current state verified"
