from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any, cast

import aioconsole
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from playwright.async_api import BrowserContext, Page

from src.agent.page_analyzer import PageAnalyzer
from src.agent.planner import Planner
from src.agent.prompts import SYSTEM_PROMPT, build_step_prompt
from src.agent.state import (
    ActionRecord,
    AgentState,
    MemoryEntry,
    PlanStep,
    build_page_fingerprint,
    create_initial_state,
    current_plan_step_id,
    mark_plan_step_done,
    refresh_history_summary,
    store_memory,
)
from src.agent.transition_analyzer import StateEvaluation, TransitionAnalysis, TransitionAnalyzer
from src.browser.manager import BrowserManager
from src.config.settings import (
    MAIN_MODEL,
    MAX_RETRIES_PER_STEP,
    MAX_STEPS,
    STEPS_BEFORE_AUTO_REPLAN,
    TEMPERATURE,
    get_openai_api_key,
)
from src.parser.page_parser import BBox, InteractiveElement, PageState
from src.security.security_layer import SecurityLayer
from src.utils.logger import logger

_BROWSER_ACTIONS: frozenset[str] = frozenset(
    {
        "click",
        "type",
        "type_text",
        "press_key",
        "navigate",
        "scroll",
        "go_back",
        "wait",
        "get_tabs",
        "switch_tab",
    }
)
_KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        "click",
        "type",
        "type_text",
        "press_key",
        "navigate",
        "scroll",
        "go_back",
        "wait",
        "get_tabs",
        "switch_tab",
        "save_memory",
        "complete_plan_step",
        "replan",
        "ask_user",
        "done",
    }
)


@dataclass
class AgentRuntime:
    browser: BrowserManager
    planner: Planner
    page_analyzer: PageAnalyzer
    transition_analyzer: TransitionAnalyzer
    security_layer: SecurityLayer
    client: AsyncOpenAI | None


def _truncate(value: Any, limit: int = 180) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_plan_for_log(plan: list[dict[str, Any]]) -> str:
    if not plan:
        return "(empty plan)"

    lines = []
    for step in plan:
        lines.append(
            f'[{step.get("status", "?")}] {step.get("id", "?")}: {step.get("description", "")}'
            f'{f" -> {step.get("result", "")}" if step.get("result") else ""}'
        )
    return "\n".join(lines)


def _sanitize_action_for_log(action: dict[str, Any]) -> dict[str, Any]:
    sanitized = {}
    for key, value in action.items():
        if key == "text":
            sanitized[key] = f"<{len(str(value))} chars: {_truncate(value, 60)}>"
        elif isinstance(value, str):
            sanitized[key] = _truncate(value, 80)
        else:
            sanitized[key] = value
    return sanitized


def _format_actions_for_log(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "[]"
    return json.dumps(
        [_sanitize_action_for_log(action) for action in actions],
        ensure_ascii=False,
        indent=2,
    )


def _should_capture_visual_context(state: AgentState, *, has_cached_summary: bool, element_count: int) -> bool:
    return bool(
        state.get("retry_count", 0) > 0
        or state.get("repeated_noop_count", 0) > 0
        or element_count == 0
        or (not has_cached_summary and state.get("steps_since_last_plan_progress", 0) > 0)
    )


def _update_summary_cache(
    cache: dict[str, str], fingerprint: str, summary: str, *, max_entries: int = 20
) -> dict[str, str]:
    updated = dict(cache)
    if fingerprint and summary:
        updated[fingerprint] = summary
    while len(updated) > max_entries:
        oldest_key = next(iter(updated))
        updated.pop(oldest_key, None)
    return updated


def _append_recent_fingerprint(
    history: list[str], fingerprint: str, *, max_entries: int = 6
) -> list[str]:
    updated = [entry for entry in history if entry]
    if fingerprint:
        updated.append(fingerprint)
    return updated[-max_entries:]


def _is_two_page_oscillation(history: list[str]) -> bool:
    if len(history) < 4:
        return False
    first, second, third, fourth = history[-4:]
    return bool(first and second and first != second and first == third and second == fourth)


def _langgraph_symbols() -> tuple[Any, Any, Any]:
    try:
        module = importlib.import_module("langgraph.graph")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Dependency 'langgraph' is not installed. Add it to the environment before running the v2 agent."
        ) from exc
    return module.StateGraph, module.START, module.END


async def run_agent_graph(task: str, page: Page, context: BrowserContext) -> str:
    api_key = get_openai_api_key()
    client = AsyncOpenAI(api_key=api_key) if api_key else None
    logger.info(f"Agent run started: task={_truncate(task, 200)}")
    logger.debug(
        f"Runtime setup: api_key_present={bool(api_key)}, initial_url={page.url}, tabs={len(context.pages)}"
    )
    runtime = AgentRuntime(
        browser=BrowserManager(page, context),
        planner=Planner(client),
        page_analyzer=PageAnalyzer(client),
        transition_analyzer=TransitionAnalyzer(client),
        security_layer=SecurityLayer(),
        client=client,
    )

    try:
        graph = build_agent_graph(runtime)
    except RuntimeError as exc:
        logger.error(f"Graph initialization failed: {exc}")
        return str(exc)

    final_state = await graph.ainvoke(create_initial_state(task))
    report = finalize_report(final_state)
    logger.info(
        "Agent run finished: "
        f"status={final_state.get('status', 'unknown')}, "
        f"steps={final_state.get('step_count', 0)}, "
        f"report={_truncate(report, 240)}"
    )
    return report


def build_agent_graph(runtime: AgentRuntime) -> Any:
    StateGraph, START, END = _langgraph_symbols()

    async def _plan(state: AgentState) -> dict[str, Any]:
        return await plan_node(state, runtime)

    async def _observe(state: AgentState) -> dict[str, Any]:
        return await observe_node(state, runtime)

    async def _think(state: AgentState) -> dict[str, Any]:
        return await think_node(state, runtime)

    async def _act(state: AgentState) -> dict[str, Any]:
        return await act_node(state, runtime)

    workflow = StateGraph(AgentState)
    workflow.add_node("plan", _plan)
    workflow.add_node("observe", _observe)
    workflow.add_node("think", _think)
    workflow.add_node("act", _act)

    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "observe")
    workflow.add_conditional_edges(
        "observe",
        route_after_observe,
        {
            "continue": "think",
            "replan": "plan",
            "end": END,
        },
    )
    workflow.add_edge("think", "act")
    workflow.add_conditional_edges(
        "act",
        route_after_act,
        {
            "continue": "observe",
            "replan": "plan",
            "end": END,
        },
    )
    return workflow.compile()


async def plan_node(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    replan_reason = state.get("last_error", "") if state.get("status") == "replan" else ""
    logger.info(
        f"PLAN: task={_truncate(state.get('task', ''), 160)}, "
        f"replan={state.get('status') == 'replan'}, "
        f"memory_entries={len(state.get('memory', []))}, "
        f"history_items={len(state.get('action_history', []))}"
    )
    if replan_reason:
        logger.debug(f"PLAN: replan reason={_truncate(replan_reason, 240)}")
    plan, reasoning = await runtime.planner.build_plan(
        task=state.get("task", ""),
        memory=state.get("memory", []),
        existing_plan=state.get("plan", []),
        replan_reason=replan_reason,
        action_history=state.get("action_history", []),
        current_url=state.get("current_url", ""),
        page_title=state.get("page_title", ""),
        page_summary=state.get("page_summary", ""),
        current_plan_step=state.get("current_plan_step", -1),
    )
    logger.info(
        f"PLAN: built {len(plan)} steps, current_step={current_plan_step_id(plan)}, "
        f"reasoning={_truncate(reasoning, 240)}"
    )
    logger.debug(f"PLAN:\n{_format_plan_for_log(cast(list[dict[str, Any]], plan))}")
    return {
        "plan": plan,
        "plan_reasoning": reasoning,
        "current_plan_step": current_plan_step_id(plan),
        "status": "running",
        "last_error": "",
    }


async def observe_node(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    logger.info(
        f"OBSERVE: step={state.get('step_count', 0) + 1}, "
        f"url={state.get('current_url', '') or '(before first observation)'}"
    )
    observation = await runtime.browser.observe(capture_screenshot=False)
    page_fingerprint = build_page_fingerprint(
        url=observation.page_state.url,
        title=observation.page_state.title,
        elements=observation.elements,
        tab_count=observation.tab_count,
    )
    summary_cache = dict(state.get("cached_page_summary", {}))
    has_cached_summary = page_fingerprint in summary_cache
    capture_visual_context = _should_capture_visual_context(
        state,
        has_cached_summary=has_cached_summary,
        element_count=len(observation.elements),
    )
    screenshot_b64 = ""
    if capture_visual_context:
        screenshot_b64 = await runtime.browser.take_annotated_screenshot(observation.page_state.elements)

    if has_cached_summary:
        page_summary = summary_cache[page_fingerprint]
    else:
        page_summary = await runtime.page_analyzer.analyze_page(
            screenshot_b64=screenshot_b64,
            elements=observation.elements,
            url=observation.page_state.url,
            title=observation.page_state.title,
            page_content=observation.page_state.content,
        )
        summary_cache = _update_summary_cache(summary_cache, page_fingerprint, page_summary)
    logger.info(
        f"OBSERVE: title={_truncate(observation.page_state.title, 100)}, "
        f"elements={len(observation.elements)}, "
        f"screenshot={'yes' if screenshot_b64 else 'no'}"
    )
    logger.debug(f"OBSERVE summary: {_truncate(page_summary, 500)}")
    updates: dict[str, Any] = {
        "current_url": observation.page_state.url,
        "page_title": observation.page_state.title,
        "page_summary": page_summary,
        "page_fingerprint": page_fingerprint,
        "interactive_elements": observation.elements,
        "screenshot_b64": screenshot_b64,
        "status": state.get("status", "running"),
        "last_error": "",
        "transition_summary": "",
        "needs_transition_analysis": False,
        "cached_page_summary": summary_cache,
    }
    recent_page_fingerprints = _append_recent_fingerprint(
        list(state.get("recent_page_fingerprints", [])),
        page_fingerprint,
    )
    updates["recent_page_fingerprints"] = recent_page_fingerprints
    plan = [cast(PlanStep, step.copy()) for step in state.get("plan", [])]
    memory = [cast(MemoryEntry, entry.copy()) for entry in state.get("memory", [])]
    current_plan_step = state.get("current_plan_step", -1)
    status = state.get("status", "running")
    final_report = state.get("final_report", "")
    last_error = state.get("last_error", "")
    progress_made = False
    step_completed = False
    summary_notes: list[str] = []
    repeated_noop_count = state.get("repeated_noop_count", 0)
    oscillating_pages = _is_two_page_oscillation(recent_page_fingerprints)
    pending_transition_check = bool(
        state.get("needs_transition_analysis", False) and state.get("last_action") is not None
    )

    if pending_transition_check:
        after_state = AgentState(
            current_url=observation.page_state.url,
            page_title=observation.page_state.title,
            page_summary=page_summary,
            page_fingerprint=page_fingerprint,
            interactive_elements=observation.elements,
        )
        analysis = await runtime.transition_analyzer.analyze_transition(
            task=state.get("task", ""),
            plan=state.get("plan", []),
            current_plan_step=state.get("current_plan_step", -1),
            last_action=state.get("last_action"),
            last_action_result=state.get("last_action_result"),
            before_state=state,
            after_state=after_state,
            allow_llm=bool(
                state.get("retry_count", 0) > 0
                or state.get("steps_since_last_plan_progress", 0) > 0
            ),
        )
        logger.info(
            "TRANSITION: "
            f"significant_change={analysis['significant_change']}, "
            f"progress_made={analysis['progress_made']}, "
            f"complete_step={analysis['complete_current_step']}, "
            f"task_completed={analysis['task_completed']}"
        )
        logger.debug(f"TRANSITION reasoning: {_truncate(analysis['reasoning'], 500)}")
        if analysis["memory_updates"]:
            logger.debug(f"TRANSITION memory_updates: {analysis['memory_updates']}")

        if analysis["reasoning"]:
            summary_notes.append(f"transition: {analysis['reasoning']}")

        (
            plan,
            current_plan_step,
            memory,
            progress_delta,
            step_completed_delta,
            task_completed,
            report_from_analysis,
        ) = _apply_semantic_analysis(
            state=state,
            plan=plan,
            current_plan_step=current_plan_step,
            memory=memory,
            analysis=analysis,
            source=f"transition after {render_action_name(state.get('last_action') or {})}",
            allow_step_completion=not step_completed,
        )
        progress_made = progress_made or progress_delta
        step_completed = step_completed or step_completed_delta
        repeated_noop_count = 0 if analysis["significant_change"] else repeated_noop_count + 1
        updates["last_action"] = None
        updates["last_action_result"] = None
        if oscillating_pages and not progress_made and status == "running":
            status = "replan"
            last_error = (
                "Detected oscillation between two page states without plan progress. "
                "Choose a different action or rebuild the plan from the current page."
            )
            summary_notes.append("transition: detected A-B-A-B page oscillation without progress")
            logger.warning(
                "OBSERVE: scheduling replan after detecting two-page oscillation without progress"
            )
        if task_completed:
            status = "done"
            final_report = report_from_analysis
            last_error = ""

    elif status == "running":
        state_evaluation = await runtime.transition_analyzer.evaluate_current_state(
            task=state.get("task", ""),
            plan=plan,
            current_plan_step=current_plan_step,
            memory=memory,
            state=AgentState(
                task=state.get("task", ""),
                plan=plan,
                current_plan_step=current_plan_step,
                memory=memory,
                current_url=observation.page_state.url,
                page_title=observation.page_state.title,
                page_summary=page_summary,
                page_fingerprint=page_fingerprint,
                interactive_elements=observation.elements,
            ),
            allow_llm=bool(
                state.get("retry_count", 0) > 0
                or state.get("steps_since_last_plan_progress", 0) > 0
                or state.get("repeated_noop_count", 0) > 0
            ),
        )
        logger.info(
            "STATE_CHECK: "
            f"progress_made={state_evaluation['progress_made']}, "
            f"complete_step={state_evaluation['complete_current_step']}, "
            f"task_completed={state_evaluation['task_completed']}"
        )
        logger.debug(f"STATE_CHECK reasoning: {_truncate(state_evaluation['reasoning'], 500)}")
        if state_evaluation["memory_updates"]:
            logger.debug(f"STATE_CHECK memory_updates: {state_evaluation['memory_updates']}")
        if state_evaluation["reasoning"]:
            summary_notes.append(f"state: {state_evaluation['reasoning']}")

        (
            plan,
            current_plan_step,
            memory,
            progress_delta,
            step_completed_delta,
            task_completed,
            report_from_evaluation,
        ) = _apply_semantic_analysis(
            state=state,
            plan=plan,
            current_plan_step=current_plan_step,
            memory=memory,
            analysis=state_evaluation,
            source="state evaluation",
            allow_step_completion=not step_completed,
        )
        progress_made = progress_made or progress_delta
        step_completed = step_completed or step_completed_delta
        if task_completed:
            status = "done"
            final_report = report_from_evaluation
            last_error = ""

    if progress_made or status == "done":
        repeated_noop_count = 0

    updates["plan"] = plan
    updates["current_plan_step"] = current_plan_step
    updates["memory"] = memory
    updates["status"] = status
    updates["final_report"] = final_report
    updates["last_error"] = last_error
    updates["transition_summary"] = "\n".join(summary_notes)
    updates["repeated_noop_count"] = repeated_noop_count

    if status == "done":
        updates["steps_since_last_plan_progress"] = 0
        return updates

    if progress_made:
        updates["steps_since_last_plan_progress"] = 0
        updates["last_error"] = ""
    elif pending_transition_check:
        new_steps_since = state.get("steps_since_last_plan_progress", 0) + 1
        updates["steps_since_last_plan_progress"] = new_steps_since
        if new_steps_since >= STEPS_BEFORE_AUTO_REPLAN:
            updates["status"] = "replan"
            updates["last_error"] = (
                summary_notes[-1]
                if summary_notes
                else f"No meaningful progress after {new_steps_since} observed transitions."
            )
            logger.warning(
                f"OBSERVE: scheduling replan after {new_steps_since} transitions without progress"
            )
    else:
        updates["steps_since_last_plan_progress"] = state.get("steps_since_last_plan_progress", 0)

    return updates


def _apply_semantic_analysis(
    *,
    state: AgentState,
    plan: list[PlanStep],
    current_plan_step: int,
    memory: list[MemoryEntry],
    analysis: TransitionAnalysis | StateEvaluation,
    source: str,
    allow_step_completion: bool,
) -> tuple[list[PlanStep], int, list[MemoryEntry], bool, bool, bool, str]:
    progress_made = bool(analysis["progress_made"])
    step_completed = False

    for memory_item in analysis["memory_updates"]:
        memory = store_memory(
            memory,
            key=memory_item["key"],
            value=memory_item["value"],
            source=source,
        )

    if allow_step_completion and analysis["complete_current_step"] and 0 <= current_plan_step < len(plan):
        completed_step = current_plan_step
        plan, current_plan_step, applied = mark_plan_step_done(
            cast(Any, plan),
            current_plan_step,
            analysis["step_result"] or analysis["reasoning"],
        )
        if applied:
            logger.info(
                f"AUTO_PLAN_SYNC: completed step {completed_step} from {source}"
            )
        progress_made = progress_made or applied
        step_completed = applied

    task_completed = bool(analysis["task_completed"])
    final_report = ""
    if task_completed:
        final_report = (
            analysis["final_report"]
            or analysis["step_result"]
            or "Task completed after semantic state evaluation."
        )

    return (
        plan,
        current_plan_step,
        memory,
        progress_made,
        step_completed,
        task_completed,
        final_report,
    )


async def think_node(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    if runtime.client is None:
        actions = [
            {
                "action": "done",
                "summary": "OPENAI_API_KEY не настроен, поэтому v2-агент не может принимать решения.",
            }
        ]
        logger.warning("THINK: no OpenAI client available, forcing done action")
        return {
            "reasoning": "LLM client unavailable",
            "planned_actions": actions,
            "next_action": actions[0],
        }

    prompt = build_step_prompt(state)
    logger.info(
        f"THINK: step={state.get('step_count', 0) + 1}, "
        f"current_plan_step={state.get('current_plan_step', -1)}, "
        f"memory_entries={len(state.get('memory', []))}, "
        f"elements={len(state.get('interactive_elements', []))}"
    )
    logger.debug(f"THINK prompt chars={len(prompt)}")
    messages: list[ChatCompletionMessageParam] = [
        cast(ChatCompletionMessageParam, {"role": "system", "content": SYSTEM_PROMPT})
    ]
    screenshot_b64 = state.get("screenshot_b64", "")

    if screenshot_b64:
        messages.append(
            cast(
                ChatCompletionMessageParam,
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{screenshot_b64}",
                                "detail": "low",
                            },
                        },
                    ],
                },
            )
        )
    else:
        messages.append(cast(ChatCompletionMessageParam, {"role": "user", "content": prompt}))

    try:
        response = await runtime.client.chat.completions.create(
            model=MAIN_MODEL,
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
            messages=messages,
        )
        raw = response.choices[0].message.content or "{}"
        logger.debug(f"THINK raw response: {_truncate(raw, 1200)}")
        reasoning, actions = parse_think_response(raw)
    except Exception as exc:
        reasoning = f"LLM error: {exc}"
        actions = [fallback_action(state, reason="LLM error")]
        logger.warning(f"THINK: model call failed, using fallback action: {exc}")

    if not actions:
        actions = [fallback_action(state, reason="empty action list")]
        logger.warning("THINK: model returned no actions, using fallback action")

    next_action = None
    for action in actions:
        if action.get("action") in _BROWSER_ACTIONS or action.get("action") in {
            "ask_user",
            "done",
            "replan",
        }:
            next_action = action
            break

    logger.info(
        f"THINK: reasoning={_truncate(reasoning, 240)}, next_action={_truncate(next_action, 200)}"
    )
    logger.debug(f"THINK actions:\n{_format_actions_for_log(actions)}")

    return {
        "reasoning": reasoning,
        "planned_actions": actions,
        "next_action": next_action,
        "last_error": "",
    }


async def act_node(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    actions = list(state.get("planned_actions", []))
    logger.info(
        f"ACT: step={state.get('step_count', 0) + 1}, "
        f"actions={len(actions)}, "
        f"status={state.get('status', 'running')}, "
        f"retry_count={state.get('retry_count', 0)}"
    )
    logger.debug(f"ACT input actions:\n{_format_actions_for_log(actions)}")
    updates: dict[str, Any] = {
        "planned_actions": [],
        "next_action": None,
        "step_count": state.get("step_count", 0) + 1,
        "last_action": None,
        "last_action_result": None,
        "needs_transition_analysis": False,
    }

    if not actions:
        logger.warning("ACT: no actions to execute")
        history = list(state.get("action_history", []))
        history.append(
            ActionRecord(
                step=updates["step_count"],
                action="noop",
                result="No actions to execute",
                success=False,
            )
        )
        updates["action_history"] = history
        updates["history_summary"] = refresh_history_summary(history)
        updates["last_error"] = "No actions to execute"
        updates["retry_count"] = state.get("retry_count", 0) + 1
        updates["steps_since_last_plan_progress"] = state.get("steps_since_last_plan_progress", 0) + 1
        return updates

    plan = [cast(PlanStep, step.copy()) for step in state.get("plan", [])]
    memory = [cast(MemoryEntry, entry.copy()) for entry in state.get("memory", [])]
    history = list(state.get("action_history", []))
    current_step = state.get("current_plan_step", -1)
    current_steps_without_progress = state.get("steps_since_last_plan_progress", 0)
    retry_count = state.get("retry_count", 0)
    repeated_noop_count = state.get("repeated_noop_count", 0)
    status = state.get("status", "running")
    final_report = state.get("final_report", "")
    last_error = ""
    last_action_fingerprint = str(state.get("last_action_fingerprint", ""))
    last_action_signature = str(state.get("last_action_signature", ""))
    user_response = None
    progress_made = False
    browser_action_executed = False
    unresolved_progress = False

    for action in actions:
        normalized = normalize_action(action)
        action_name = normalized.get("action", "")

        if action_name == "save_memory":
            key = str(normalized.get("key", "")).strip()
            value = str(normalized.get("value", "")).strip()
            reason = str(normalized.get("reasoning", "")).strip()
            logger.info(
                f"SAVE_MEMORY: key={key}, value={_truncate(value, 160)}, reason={_truncate(reason, 160)}"
            )
            memory = store_memory(
                memory,
                key=key,
                value=value,
                source=reason or f"step {updates['step_count']}",
            )
            progress_made = True
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action=f"save_memory({key})",
                    result=value[:80],
                    success=True,
                )
            )
            continue

        if action_name == "complete_plan_step":
            step_id = int(normalized.get("step_id", current_step))
            result_text = str(normalized.get("result", "")).strip()
            logger.info(
                f"COMPLETE_PLAN_STEP: step_id={step_id}, result={_truncate(result_text, 200)}"
            )
            plan, current_step, applied = mark_plan_step_done(cast(Any, plan), step_id, result_text)
            progress_made = progress_made or applied
            if applied:
                retry_count = 0
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action=f"complete_plan_step({step_id})",
                    result=result_text or "completed",
                    success=applied,
                )
            )
            continue

        if action_name == "replan":
            status = "replan"
            last_error = str(normalized.get("reason", "Требуется перепланирование"))
            logger.warning(f"REPLAN requested: {_truncate(last_error, 240)}")
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action="replan",
                    result=last_error,
                    success=True,
                )
            )
            break

        if action_name == "ask_user":
            question = str(normalized.get("question", "")).strip() or "Уточните, как продолжить?"
            logger.info(f"ASK_USER: {_truncate(question, 240)}")
            answer = (await aioconsole.ainput(f"\n[Agent] {question}\nYour answer: ")).strip()
            logger.debug(f"ASK_USER answer: {_truncate(answer, 240)}")
            user_response = answer
            memory = store_memory(
                memory,
                key=f"user_response_step_{updates['step_count']}",
                value=answer,
                source=question,
            )
            progress_made = True
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action="ask_user",
                    result=question,
                    success=True,
                )
            )
            break

        if action_name == "done":
            status = "done"
            final_report = str(normalized.get("summary", "")).strip() or "Task completed"
            logger.info(f"DONE: {_truncate(final_report, 240)}")
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action="done",
                    result=final_report[:120],
                    success=True,
                )
            )
            break

        if browser_action_executed:
            break

        action_signature = render_action_name(normalized)
        if (
            repeated_noop_count >= 1
            and last_action_fingerprint
            and last_action_fingerprint == str(state.get("page_fingerprint", ""))
            and last_action_signature == action_signature
        ):
            status = "replan"
            last_error = (
                "Repeated the same browser action on an unchanged page fingerprint. "
                "Choose an alternative action or replan."
            )
            logger.warning(f"ACT: blocked repeated no-op action {action_signature}")
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action="replan",
                    result=last_error,
                    success=True,
                )
            )
            break

        try:
            logger.info(f"BROWSER_ACTION: {render_action_name(normalized)}")
            logger.debug(
                f"BROWSER_ACTION payload: {json.dumps(_sanitize_action_for_log(normalized), ensure_ascii=False)}"
            )
            await maybe_confirm_action(runtime, state, normalized)
            result = await execute_browser_action(runtime, state, normalized)
            logger.info(f"BROWSER_ACTION result: {_truncate(result.get('description', 'OK'), 200)}")
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action=render_action_name(normalized),
                    result=str(result.get("description", "OK")),
                    success=True,
                )
            )
            retry_count = 0
            browser_action_executed = True
            unresolved_progress = True
            last_action_fingerprint = str(state.get("page_fingerprint", ""))
            last_action_signature = action_signature
            updates["last_action"] = normalized
            updates["last_action_result"] = result
            updates["needs_transition_analysis"] = True
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                f"BROWSER_ACTION failed: action={render_action_name(normalized)}, error={_truncate(last_error, 240)}"
            )
            history.append(
                ActionRecord(
                    step=updates["step_count"],
                    action=render_action_name(normalized),
                    result=last_error[:160],
                    success=False,
                )
            )
            retry_count += 1
            current_steps_without_progress += 1
            if retry_count >= MAX_RETRIES_PER_STEP:
                status = "replan"
            break

    updates["plan"] = plan
    updates["current_plan_step"] = current_step
    updates["memory"] = memory
    updates["action_history"] = history
    updates["history_summary"] = refresh_history_summary(history)
    updates["retry_count"] = retry_count
    updates["repeated_noop_count"] = repeated_noop_count
    updates["last_action_fingerprint"] = last_action_fingerprint
    updates["last_action_signature"] = last_action_signature
    if progress_made:
        updates["steps_since_last_plan_progress"] = 0
    elif unresolved_progress:
        updates["steps_since_last_plan_progress"] = current_steps_without_progress
    else:
        updates["steps_since_last_plan_progress"] = current_steps_without_progress + 1
    updates["status"] = status
    updates["final_report"] = final_report
    updates["user_response"] = user_response
    updates["last_error"] = last_error
    logger.debug(
        "ACT result: "
        f"status={status}, current_plan_step={current_step}, "
        f"memory_entries={len(memory)}, retry_count={retry_count}, "
        f"steps_since_progress={updates['steps_since_last_plan_progress']}, "
        f"last_error={_truncate(last_error, 180)}"
    )
    return updates


def route_after_act(state: AgentState) -> str:
    status = state.get("status", "running")
    if status in {"done", "need_input", "error"}:
        logger.info(
            f"ROUTE: end due to terminal status={status}, step_count={state.get('step_count', 0)}"
        )
        return "end"
    if state.get("step_count", 0) >= MAX_STEPS:
        logger.warning(f"ROUTE: end due to step limit {state.get('step_count', 0)}/{MAX_STEPS}")
        return "end"
    if status == "replan":
        logger.info(
            f"ROUTE: replan due to status=replan, reason={_truncate(state.get('last_error', ''), 200)}"
        )
        return "replan"
    logger.debug(
        f"ROUTE: continue, status={status}, step_count={state.get('step_count', 0)}, "
        f"steps_since_progress={state.get('steps_since_last_plan_progress', 0)}"
    )
    return "continue"


def route_after_observe(state: AgentState) -> str:
    status = state.get("status", "running")
    if status in {"done", "need_input", "error"}:
        logger.info(
            f"ROUTE_OBSERVE: end due to terminal status={status}, step_count={state.get('step_count', 0)}"
        )
        return "end"
    if status == "replan":
        logger.info(
            f"ROUTE_OBSERVE: replan due to status=replan, reason={_truncate(state.get('last_error', ''), 200)}"
        )
        return "replan"
    return "continue"


def finalize_report(state: AgentState) -> str:
    final_report = state.get("final_report", "")
    if final_report:
        return str(final_report)

    if state.get("status") == "done":
        return "Task completed"

    if state.get("step_count", 0) >= MAX_STEPS:
        return (
            "Агент остановлен по лимиту шагов. "
            f"Текущий URL: {state.get('current_url', '') or 'unknown'}."
        )

    last_error = state.get("last_error", "")
    if last_error:
        return f"Task stopped with error: {last_error}"

    return "Task finished without a final report."


def parse_think_response(raw: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(f"THINK parse error: invalid JSON: {exc}")
        return f"Invalid JSON: {exc}", []

    if isinstance(data, list):
        return "", [normalize_action(item) for item in data if isinstance(item, dict)]

    if not isinstance(data, dict):
        logger.warning(f"THINK parse error: unexpected response shape {type(data).__name__}")
        return "Unexpected response shape", []

    reasoning = str(data.get("reasoning", "")).strip()
    actions_raw = data.get("actions", data.get("action", []))
    if isinstance(actions_raw, dict):
        actions_raw = [actions_raw]
    if not isinstance(actions_raw, list):
        return reasoning, []

    normalized = []
    for item in actions_raw:
        if not isinstance(item, dict):
            continue
        normalized.append(normalize_action(item))
    return reasoning, normalized


def normalize_action(action: dict[str, Any]) -> dict[str, Any]:
    if "action" in action and str(action.get("action", "")).strip():
        normalized = dict(action)
    else:
        normalized = _normalize_shorthand_action(action)

    action_name = str(normalized.get("action", "")).strip()
    if action_name == "type_text":
        action_name = "type"
    normalized["action"] = action_name

    if "ref" in normalized and "element_id" not in normalized:
        normalized["element_id"] = normalized["ref"]
    if "element_id" in normalized:
        normalized["element_id"] = _coerce_numeric(normalized["element_id"])
    if "step_id" in normalized:
        normalized["step_id"] = _coerce_numeric(normalized["step_id"])
    if "index" in normalized:
        normalized["index"] = _coerce_numeric(normalized["index"])
    return normalized


def _normalize_shorthand_action(action: dict[str, Any]) -> dict[str, Any]:
    recognized_keys = [key for key in action if key in _KNOWN_ACTIONS]
    if len(recognized_keys) != 1:
        return dict(action)

    action_name = recognized_keys[0]
    payload = action[action_name]
    extras = {key: value for key, value in action.items() if key != action_name}
    normalized: dict[str, Any] = {"action": action_name, **extras}

    if isinstance(payload, dict):
        normalized.update(payload)
        return normalized

    field_name = {
        "click": "element_id",
        "type": "text",
        "type_text": "text",
        "press_key": "key",
        "navigate": "url",
        "scroll": "direction",
        "wait": "seconds",
        "switch_tab": "index",
        "complete_plan_step": "step_id",
        "ask_user": "question",
        "done": "summary",
        "replan": "reason",
    }.get(action_name)

    if field_name:
        normalized[field_name] = payload
    return normalized


def _coerce_numeric(value: Any) -> Any:
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def fallback_action(state: AgentState, *, reason: str) -> dict[str, Any]:
    if state.get("interactive_elements"):
        return {
            "action": "scroll",
            "direction": "down",
            "amount": 500,
            "reasoning": f"Fallback action due to {reason}",
        }
    return {"action": "wait", "seconds": 1.0, "reasoning": f"Fallback action due to {reason}"}


async def execute_browser_action(
    runtime: AgentRuntime,
    state: AgentState,
    action: dict[str, Any],
) -> dict[str, Any]:
    action_name = action.get("action")
    elements = list(state.get("interactive_elements", []))

    if action_name == "click":
        return await runtime.browser.click(int(action["element_id"]), elements)
    if action_name == "type":
        return await runtime.browser.type_text(
            int(action["element_id"]),
            str(action.get("text", "")),
            elements,
            press_enter=bool(action.get("press_enter", False)),
        )
    if action_name == "press_key":
        return await runtime.browser.press_key(str(action.get("key", "")))
    if action_name == "navigate":
        return await runtime.browser.navigate(str(action.get("url", "")))
    if action_name == "scroll":
        return await runtime.browser.scroll(
            str(action.get("direction", "down")),
            int(action.get("amount", 500)),
        )
    if action_name == "go_back":
        return await runtime.browser.go_back()
    if action_name == "wait":
        return await runtime.browser.wait(float(action.get("seconds", 2.0)))
    if action_name == "get_tabs":
        return await runtime.browser.get_tabs()
    if action_name == "switch_tab":
        return await runtime.browser.switch_tab(int(action.get("index", 0)))

    raise ValueError(f"Unsupported browser action: {action_name}")


async def maybe_confirm_action(
    runtime: AgentRuntime,
    state: AgentState,
    action: dict[str, Any],
) -> None:
    page_state = page_state_from_state(state)
    tool_name, args = security_view(action)
    if runtime.security_layer.is_dangerous(tool_name, args, page_state):
        logger.warning(
            f"SECURITY: confirmation required for {tool_name} with args={json.dumps(args, ensure_ascii=False)}"
        )
        allowed = await runtime.security_layer.request_confirmation(tool_name, args)
        if not allowed:
            logger.warning(f"SECURITY: user denied action {tool_name}")
            raise RuntimeError("Action denied by user")
        logger.info(f"SECURITY: user allowed action {tool_name}")


def security_view(action: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = str(action.get("action", ""))
    if name == "type":
        return (
            "type_text",
            {
                "ref": int(action.get("element_id", -1)),
                "text": str(action.get("text", "")),
                "press_enter": bool(action.get("press_enter", False)),
            },
        )
    if name == "click":
        return "click", {"ref": int(action.get("element_id", -1))}
    if name == "navigate":
        return "navigate", {"url": str(action.get("url", ""))}
    if name == "scroll":
        return "scroll", {"direction": str(action.get("direction", "down"))}
    if name == "press_key":
        return "press_key", {"key": str(action.get("key", ""))}
    return name, dict(action)


def page_state_from_state(state: AgentState) -> PageState:
    elements = []
    for snapshot in state.get("interactive_elements", []):
        bbox_dict = snapshot.get("bbox")
        bbox = None
        if bbox_dict:
            bbox = BBox(
                x=int(bbox_dict["x"]),
                y=int(bbox_dict["y"]),
                width=int(bbox_dict["width"]),
                height=int(bbox_dict["height"]),
            )
        elements.append(
            InteractiveElement(
                ref=int(snapshot.get("index", snapshot.get("ref", -1))),
                tag=str(snapshot.get("tag", "")),
                role=str(snapshot.get("role", "")),
                text=str(snapshot.get("text", "")),
                aria_label=str(snapshot.get("aria_label", "")),
                placeholder=str(snapshot.get("placeholder", "")),
                href=str(snapshot.get("href", "")),
                input_type=str(snapshot.get("input_type", "")),
                value=str(snapshot.get("value", "")),
                disabled=bool(snapshot.get("disabled", False)),
                bbox=bbox,
            )
        )
    return PageState(
        url=str(state.get("current_url", "")),
        title=str(state.get("page_title", "")),
        content=str(state.get("page_summary", "")),
        elements=elements,
    )


def render_action_name(action: dict[str, Any]) -> str:
    name = str(action.get("action", ""))
    if name in {"click", "type"}:
        return f"{name}[{action.get('element_id', '?')}]"
    if name == "navigate":
        return f"navigate({action.get('url', '')})"
    if name == "scroll":
        return f"scroll({action.get('direction', 'down')})"
    if name == "switch_tab":
        return f"switch_tab({action.get('index', '?')})"
    return name
