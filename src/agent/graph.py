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
    create_initial_state,
    current_plan_step_id,
    mark_plan_step_done,
    refresh_history_summary,
    store_memory,
)
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
    {"click", "type", "type_text", "press_key", "navigate", "scroll", "go_back", "wait"}
)


@dataclass
class AgentRuntime:
    browser: BrowserManager
    planner: Planner
    page_analyzer: PageAnalyzer
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
    workflow.add_edge("observe", "think")
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
    observation = await runtime.browser.observe()
    page_summary = await runtime.page_analyzer.analyze_page(
        screenshot_b64=observation.screenshot_b64,
        elements=observation.elements,
        url=observation.page_state.url,
        title=observation.page_state.title,
    )
    logger.info(
        f"OBSERVE: title={_truncate(observation.page_state.title, 100)}, "
        f"elements={len(observation.elements)}, "
        f"screenshot={'yes' if observation.screenshot_b64 else 'no'}"
    )
    logger.debug(f"OBSERVE summary: {_truncate(page_summary, 500)}")
    return {
        "current_url": observation.page_state.url,
        "page_title": observation.page_state.title,
        "page_summary": page_summary,
        "interactive_elements": observation.elements,
        "screenshot_b64": observation.screenshot_b64,
    }


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

    plan = cast(list[dict[str, Any]], [step.copy() for step in state.get("plan", [])])
    memory = list(state.get("memory", []))
    history = list(state.get("action_history", []))
    current_step = state.get("current_plan_step", -1)
    steps_without_progress = state.get("steps_since_last_plan_progress", 0) + 1
    retry_count = state.get("retry_count", 0)
    status = state.get("status", "running")
    final_report = state.get("final_report", "")
    last_error = ""
    user_response = None
    progress_made = False
    browser_action_executed = False

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
                steps_without_progress = 0
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
            if retry_count >= MAX_RETRIES_PER_STEP:
                status = "replan"
            break

    updates["plan"] = plan
    updates["current_plan_step"] = current_step
    updates["memory"] = memory
    updates["action_history"] = history
    updates["history_summary"] = refresh_history_summary(history)
    updates["retry_count"] = retry_count
    updates["steps_since_last_plan_progress"] = 0 if progress_made else steps_without_progress
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
    if state.get("steps_since_last_plan_progress", 0) >= STEPS_BEFORE_AUTO_REPLAN:
        logger.warning(
            "ROUTE: auto replan due to no progress "
            f"for {state.get('steps_since_last_plan_progress', 0)} steps"
        )
        return "replan"
    logger.debug(
        f"ROUTE: continue, status={status}, step_count={state.get('step_count', 0)}, "
        f"steps_since_progress={state.get('steps_since_last_plan_progress', 0)}"
    )
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
    if "action" in action:
        normalized = dict(action)
    elif len(action) == 1:
        name, payload = next(iter(action.items()))
        if isinstance(payload, dict):
            normalized = {"action": name, **payload}
        else:
            normalized = {"action": name}
    else:
        normalized = dict(action)

    action_name = str(normalized.get("action", "")).strip()
    if action_name == "type_text":
        action_name = "type"
    normalized["action"] = action_name

    if "ref" in normalized and "element_id" not in normalized:
        normalized["element_id"] = normalized["ref"]
    return normalized


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
    return name
