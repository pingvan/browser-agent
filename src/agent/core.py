from __future__ import annotations

import json
from typing import Any, cast

import aioconsole
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from playwright.async_api import BrowserContext, Page

from src.agent.inspection import DomInspector
from src.agent.loop_detector import LoopDetector
from src.agent.message_manager import MessageManager
from src.agent.prompts import MAIN_AGENT_SYSTEM_PROMPT
from src.agent.state import (
    AgentState,
    InspectionResult,
    StepPacket,
    append_recent_item,
    append_step_history,
    build_page_fingerprint,
    create_initial_state,
    store_memory,
)
from src.agent.tool_registry import ToolRegistry
from src.browser.manager import BrowserManager
from src.config.settings import (
    MAIN_MODEL,
    MAX_RETRIES_PER_STEP,
    MAX_STEPS,
    MAX_STUCK_STEPS,
    RAW_PAGE_TEXT_CHAR_BUDGET,
    TEMPERATURE,
    get_openai_api_key,
)
from src.parser.page_parser import PageState
from src.security.security_layer import SecurityLayer
from src.utils.logger import logger


def _extract_page_text(page_content: str) -> str:
    if not page_content:
        return ""
    marker = "## Page Content (summary)"
    start = page_content.find(marker)
    if start == -1:
        return " ".join(page_content.split())[:RAW_PAGE_TEXT_CHAR_BUDGET]
    text = page_content[start + len(marker):].strip()
    next_marker = text.find("## Interactive Elements")
    if next_marker != -1:
        text = text[:next_marker]
    return " ".join(text.split())[:RAW_PAGE_TEXT_CHAR_BUDGET]


def _truncate(value: Any, limit: int = 180) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


class Agent:
    def __init__(
        self,
        *,
        task: str,
        page: Page | None = None,
        context: BrowserContext | None = None,
        browser: Any | None = None,
        client: Any | None = None,
        security_layer: SecurityLayer | None = None,
        # summarizer: Any | None = None,
        dom_inspector: DomInspector | None = None,
        message_manager: MessageManager | None = None,
        tool_registry: ToolRegistry | None = None,
        loop_detector: LoopDetector | None = None,
    ) -> None:
        if browser is None:
            if page is None or context is None:
                raise ValueError("Either browser or both page/context must be provided")
            browser = BrowserManager(page, context)

        self.task = task
        self.browser = browser
        self.client = client
        self.security_layer = security_layer or SecurityLayer()
        # self.summarizer = summarizer
        self.dom_inspector = dom_inspector or DomInspector(client)
        self.message_manager = message_manager or MessageManager()
        self.tool_registry = tool_registry or ToolRegistry()
        self.loop_detector = loop_detector or LoopDetector()

    async def run(self) -> str:
        logger.info(f"Agent run started: task={_truncate(self.task, 200)}")
        state = create_initial_state(self.task)

        if self.client is None:
            message = "OPENAI_API_KEY is not configured, so the browser agent cannot run."
            logger.error(message)
            return message

        await self._observe(state, force_visual=False)

        while state.get("step_count", 0) < MAX_STEPS:
            state["step_count"] = state.get("step_count", 0) + 1
            logger.info(
                f"AGENT_STEP: step={state['step_count']}, url={_truncate(state.get('current_url', ''), 120)}, "
                f"subtask={_truncate(state.get('current_subtask', '') or '(not set)', 120)}"
            )

            messages = self.message_manager.build_messages(
                state,
                system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
            )
            tool_calls = await self._decide(messages, state)
            if tool_calls is None:
                if state.get("status") in {"done", "error"}:
                    break
                await self._observe(state, force_visual=state.get("retry_count", 0) > 0)
                continue

            await self._execute_tool_calls(state, tool_calls)
            if state.get("status") in {"done", "error"}:
                break

            await self._observe(
                state,
                force_visual=bool(
                    state.get("retry_count", 0) > 0 or state.get("consecutive_stuck_steps", 0) > 0
                ),
            )

            page_loop_hint = self.loop_detector.detect_page_loop(
                state.get("recent_page_fingerprints", [])
            )
            if page_loop_hint:
                self._set_stuck_hint(state, page_loop_hint)
            elif not state.get("last_error"):
                state["stuck_hint"] = ""

            if state.get("consecutive_stuck_steps", 0) >= MAX_STUCK_STEPS:
                state["status"] = "error"
                state["final_report"] = (
                    "Agent stopped after repeated stuck behavior without making meaningful progress."
                )
                break

            if state.get("consecutive_failures", 0) >= MAX_RETRIES_PER_STEP:
                state["status"] = "error"
                state["final_report"] = (
                    "Agent stopped after repeated invalid or failed actions."
                )
                break

        if state.get("status") == "done":
            logger.info(
                f"Agent run finished: status=done, steps={state.get('step_count', 0)}, "
                f"report={_truncate(state.get('final_report', ''), 240)}"
            )
            return state.get("final_report", "Task completed.")

        if not state.get("final_report"):
            state["final_report"] = (
                state.get("last_error")
                or f"Agent stopped after reaching the step limit ({MAX_STEPS})."
            )
        logger.info(
            f"Agent run finished: status={state.get('status', 'error')}, "
            f"steps={state.get('step_count', 0)}, "
            f"report={_truncate(state.get('final_report', ''), 240)}"
        )
        return state.get("final_report", "")

    async def _observe(self, state: AgentState, *, force_visual: bool) -> None:
        previous_fingerprint = state.get("page_fingerprint", "")
        observation = await self.browser.observe(capture_screenshot=True)
        fingerprint = build_page_fingerprint(
            url=observation.page_state.url,
            title=observation.page_state.title,
            elements=observation.elements,
            tab_count=observation.tab_count,
        )
        state["current_url"] = observation.page_state.url
        state["page_title"] = observation.page_state.title
        state["page_content"] = observation.page_state.content
        state["page_fingerprint"] = fingerprint
        state["last_screenshot_b64"] = observation.screenshot_b64
        state["interactive_elements"] = observation.elements
        state["last_observation"] = {
            "url": observation.page_state.url,
            "title": observation.page_state.title,
            "content": observation.page_state.content,
            "tab_count": observation.tab_count,
        }
        state["recent_page_fingerprints"] = append_recent_item(
            state.get("recent_page_fingerprints", []),
            fingerprint,
        )
        if previous_fingerprint and previous_fingerprint != fingerprint:
            state["last_dom_inspection"] = InspectionResult(
                question="",
                answer="",
                observations=[],
                candidate_elements=[],
                source="dom",
                fingerprint="",
            )
        state["prompt_injection_warnings"] = self.security_layer.check_prompt_injection(
            observation.page_state
        )

        state["page_text_excerpt"] = _extract_page_text(observation.page_state.content)
        logger.debug(
            f"OBSERVE: title={_truncate(observation.page_state.title, 120)}, "
            f"elements={len(observation.elements)}, fingerprint={fingerprint}"
        )

    async def _decide(
        self,
        messages: list[dict[str, Any]],
        state: AgentState,
    ) -> list[Any] | None:
        client = self.client
        if client is None:
            self._record_failure(state, action="model_call", result="LLM client is not configured.")
            return None
        request_messages = self._build_model_messages(messages, state)
        try:
            response = await client.chat.completions.create(
                model=MAIN_MODEL,
                temperature=TEMPERATURE,
                messages=cast(list[ChatCompletionMessageParam], request_messages),
                tools=cast(list[ChatCompletionToolParam], self.tool_registry.tool_definitions),
                tool_choice="auto",
            )
        except Exception as exc:
            self._record_failure(
                state,
                action="model_call",
                result=f"LLM call failed: {exc}",
            )
            return None

        message = response.choices[0].message
        tool_calls = list(message.tool_calls or [])
        logger.debug(
            f"DECIDE: tool_calls={len(tool_calls)}, content={_truncate(message.content or '', 240)}"
        )
        if not tool_calls:
            self._record_failure(
                state,
                action="model_call",
                result="Model returned no tool calls. Use tools only.",
            )
            return None
        return tool_calls

    async def _execute_tool_calls(self, state: AgentState, tool_calls: list[Any]) -> None:
        browser_action_executed = False

        for tool_call in tool_calls:
            name = str(getattr(tool_call.function, "name", "")).strip()
            tool_call_id = str(getattr(tool_call, "id", ""))
            raw_arguments = str(getattr(tool_call.function, "arguments", "") or "")

            try:
                arguments = self.tool_registry.parse_arguments(raw_arguments)
            except Exception as exc:
                self._record_failure(
                    state,
                    action=name or "invalid_tool",
                    result=f"Invalid tool arguments: {exc}",
                )
                break

            validation_error = self.tool_registry.validate(name, arguments)
            if validation_error:
                self._record_failure(state, action=name, result=validation_error)
                break

            action_signature = self.tool_registry.render_action_signature(name, arguments)
            logger.info(
                f"EXECUTE: tool={name}, args={_truncate(json.dumps(arguments, ensure_ascii=False), 240)}, "
                f"tool_call_id={tool_call_id}"
            )

            if self.tool_registry.is_browser_action(name):
                if browser_action_executed:
                    self._record_failure(
                        state,
                        action=name,
                        result="Only one browser action is allowed per step.",
                    )
                    break
                if self._should_block_browser_action(state, action_signature):
                    break
                result = await self._execute_browser_action(name, arguments, state)
                browser_action_executed = True
                state["last_action_result"] = result
                state["last_action_signature"] = action_signature
                state["last_action_fingerprint"] = state.get("page_fingerprint", "")
                state["recent_action_signatures"] = append_recent_item(
                    state.get("recent_action_signatures", []),
                    action_signature,
                )
                if result.get("success", False):
                    state["step_history"] = append_step_history(
                        state.get("step_history", []),
                        step=state.get("step_count", 0),
                        action=name,
                        result=str(result.get("description", "OK")),
                        success=True,
                        page_changed=bool(result.get("page_changed", False)),
                    )
                    state["retry_count"] = 0
                    state["invalid_tool_calls"] = 0
                    state["consecutive_failures"] = 0
                    state["last_error"] = ""
                    if result.get("page_changed", False):
                        state["consecutive_stuck_steps"] = 0
                        state["stuck_hint"] = ""
                    else:
                        self._apply_action_loop_hint(state)
                else:
                    self._record_failure(
                        state,
                        action=name,
                        result=str(result.get("error", result.get("description", "Browser action failed"))),
                        invalid_tool_call=False,
                    )
                break

            should_stop = await self._execute_state_action(name, arguments, state)
            state["recent_action_signatures"] = append_recent_item(
                state.get("recent_action_signatures", []),
                action_signature,
            )
            if should_stop:
                break

    async def _execute_state_action(
        self,
        name: str,
        arguments: dict[str, Any],
        state: AgentState,
    ) -> bool:
        if name == "step_meta":
            step_packet = StepPacket(
                step_eval=cast(Any, str(arguments["step_eval"]).strip()),
                decision_note=str(arguments["decision_note"]).strip()[:280],
                memory_candidate=str(arguments.get("memory_candidate", "")).strip()[:220],
                next_goal=str(arguments.get("next_goal", "")).strip()[:160],
            )
            state["last_step_packet"] = step_packet
            next_goal = str(step_packet.get("next_goal", "")).strip()
            if next_goal:
                state["current_subtask"] = next_goal
            logger.info(
                "STEP_META:\n"
                f"Eval: {step_packet.get('step_eval', '')}\n"
                f"Decision: {_truncate(step_packet.get('decision_note', ''), 240)}\n"
                f"Memory candidate: {_truncate(step_packet.get('memory_candidate', '') or '(none)', 240)}\n"
                f"Next goal: {_truncate(step_packet.get('next_goal', '') or '(not set)', 240)}"
            )
            state["retry_count"] = 0
            state["invalid_tool_calls"] = 0
            state["consecutive_failures"] = 0
            state["last_error"] = ""
            return False

        if name == "inspect_dom":
            question = str(arguments["question"]).strip()
            result = await self.dom_inspector.inspect(
                question=question,
                page_state=self._build_current_page_state(state),
                elements=state.get("interactive_elements", []),
                fingerprint=state.get("page_fingerprint", ""),
            )
            state["last_dom_inspection"] = result
            logger.info(
                "DOM_INSPECTION:\n"
                f"Question: {question}\n"
                f"Answer: {_truncate(result.get('answer', ''), 240)}"
            )
            state["retry_count"] = 0
            state["invalid_tool_calls"] = 0
            state["consecutive_failures"] = 0
            state["last_error"] = ""
            return False

        if name == "set_subtask":
            subtask = str(arguments["subtask"]).strip()
            state["current_subtask"] = subtask[:160]
            state["step_history"] = append_step_history(
                state.get("step_history", []),
                step=state.get("step_count", 0),
                action=name,
                result=subtask,
                success=True,
            )
            state["retry_count"] = 0
            state["invalid_tool_calls"] = 0
            state["consecutive_failures"] = 0
            state["last_error"] = ""
            return False

        if name == "save_memory":
            key = str(arguments["key"]).strip()
            value = str(arguments["value"]).strip()
            state["memory"] = store_memory(
                state.get("memory", []),
                key=key,
                value=value,
                source=state.get("current_subtask") or f"step {state.get('step_count', 0)}",
            )
            state["step_history"] = append_step_history(
                state.get("step_history", []),
                step=state.get("step_count", 0),
                action=name,
                result=f"{key}={value}",
                success=True,
            )
            state["retry_count"] = 0
            state["invalid_tool_calls"] = 0
            state["consecutive_failures"] = 0
            state["last_error"] = ""
            return False

        if name == "ask_user":
            question = str(arguments["question"]).strip() or "Уточните, как продолжить?"
            answer = (await aioconsole.ainput(f"\n[Agent] {question}\nYour answer: ")).strip()
            state["user_response"] = answer
            state["memory"] = store_memory(
                state.get("memory", []),
                key=f"user_response_step_{state.get('step_count', 0)}",
                value=answer,
                source=question,
            )
            state["step_history"] = append_step_history(
                state.get("step_history", []),
                step=state.get("step_count", 0),
                action=name,
                result=question,
                success=True,
            )
            state["retry_count"] = 0
            state["invalid_tool_calls"] = 0
            state["consecutive_failures"] = 0
            state["stuck_hint"] = ""
            state["last_error"] = ""
            return True

        if name == "done":
            summary = str(arguments["summary"]).strip() or "Task completed."
            state["status"] = "done"
            state["final_report"] = summary
            state["step_history"] = append_step_history(
                state.get("step_history", []),
                step=state.get("step_count", 0),
                action=name,
                result=summary,
                success=True,
            )
            return True

        self._record_failure(state, action=name, result="Unsupported state tool.")
        return True

    def _build_current_page_state(self, state: AgentState) -> PageState:
        return PageState(
            url=state.get("current_url", ""),
            title=state.get("page_title", ""),
            content=state.get("page_content", ""),
            elements=[],
        )

    def _build_model_messages(
        self,
        messages: list[dict[str, Any]],
        state: AgentState,
    ) -> list[dict[str, Any]]:
        screenshot_b64 = str(state.get("last_screenshot_b64", "")).strip()
        if not screenshot_b64 or len(messages) < 2:
            return messages

        system_message = messages[0]
        user_text = str(messages[1].get("content", ""))
        multimodal_user_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_b64}",
                        "detail": "high",
                    },
                },
            ],
        }
        return [system_message, multimodal_user_message]

    async def _execute_browser_action(
        self,
        name: str,
        arguments: dict[str, Any],
        state: AgentState,
    ) -> dict[str, Any]:
        observation_page_state = await self.browser.observe(capture_screenshot=False)
        if self.security_layer.is_dangerous(name, arguments, observation_page_state.page_state):
            allowed = await self.security_layer.request_confirmation(name, arguments)
            if not allowed:
                return {
                    "success": False,
                    "description": "Action rejected by the user",
                    "page_changed": False,
                    "url_before": state.get("current_url", ""),
                    "url_after": state.get("current_url", ""),
                }

        try:
            if name == "navigate":
                return await self.browser.navigate(str(arguments["url"]))
            if name == "click":
                return await self.browser.click(
                    int(arguments["element_id"]),
                    state.get("interactive_elements", []),
                )
            if name == "type_text":
                return await self.browser.type_text(
                    int(arguments["element_id"]),
                    str(arguments["text"]),
                    state.get("interactive_elements", []),
                    press_enter=bool(arguments.get("press_enter", False)),
                )
            if name == "press_key":
                return await self.browser.press_key(str(arguments["key"]))
            if name == "scroll":
                amount = int(arguments.get("amount", 500))
                return await self.browser.scroll(str(arguments["direction"]), amount)
            if name == "go_back":
                return await self.browser.go_back()
            if name == "get_tabs":
                return await self.browser.get_tabs()
            if name == "switch_tab":
                return await self.browser.switch_tab(int(arguments["index"]))
            if name == "wait":
                return await self.browser.wait(float(arguments.get("seconds", 2.0)))
        except Exception as exc:
            return {
                "success": False,
                "description": f"Browser action failed: {exc}",
                "error": str(exc),
                "page_changed": False,
                "url_before": state.get("current_url", ""),
                "url_after": state.get("current_url", ""),
            }

        return {
            "success": False,
            "description": f"Unsupported browser tool: {name}",
            "page_changed": False,
            "url_before": state.get("current_url", ""),
            "url_after": state.get("current_url", ""),
        }

    def _should_block_browser_action(self, state: AgentState, action_signature: str) -> bool:
        if (
            state.get("last_action_signature")
            and state.get("last_action_signature") == action_signature
            and state.get("last_action_fingerprint") == state.get("page_fingerprint")
            and not bool((state.get("last_action_result") or {}).get("page_changed", False))
        ):
            self._set_stuck_hint(
                state,
                "The same browser action was already attempted on this unchanged page. "
                "Choose a different action.",
            )
            state["step_history"] = append_step_history(
                state.get("step_history", []),
                step=state.get("step_count", 0),
                action=action_signature,
                result="Blocked repeated browser action on unchanged page fingerprint",
                success=False,
            )
            return True
        return False

    def _apply_action_loop_hint(self, state: AgentState) -> None:
        action_hint = self.loop_detector.detect_action_loop(state.get("recent_action_signatures", []))
        if action_hint:
            self._set_stuck_hint(state, action_hint)
        else:
            state["consecutive_stuck_steps"] = 0
            state["stuck_hint"] = ""

    def _set_stuck_hint(self, state: AgentState, hint: str) -> None:
        state["stuck_hint"] = hint
        state["consecutive_stuck_steps"] = state.get("consecutive_stuck_steps", 0) + 1

    def _record_failure(
        self,
        state: AgentState,
        *,
        action: str,
        result: str,
        invalid_tool_call: bool = True,
    ) -> None:
        state["last_error"] = result
        state["retry_count"] = state.get("retry_count", 0) + 1
        if invalid_tool_call:
            state["invalid_tool_calls"] = state.get("invalid_tool_calls", 0) + 1
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["step_history"] = append_step_history(
            state.get("step_history", []),
            step=state.get("step_count", 0),
            action=action,
            result=result,
            success=False,
        )


async def run_agent(task: str, page: Page, context: BrowserContext) -> str:
    api_key = get_openai_api_key()
    client = AsyncOpenAI(api_key=api_key) if api_key else None
    agent = Agent(task=task, page=page, context=context, client=client)
    return await agent.run()
