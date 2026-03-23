from __future__ import annotations

import json
import os
import time
from typing import Any, cast
from urllib.parse import quote_plus

import aioconsole
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from playwright.async_api import BrowserContext, Page

from src.agent.loop_detector import LoopDetector
from src.agent.message_manager import MessageManager
from src.agent.prompts import MAIN_AGENT_SYSTEM_PROMPT
from src.agent.schema import AgentOutput
from src.agent.state import (
    ActiveModalSnapshot,
    AgentState,
    BBox,
    ElementSnapshot,
    ViewportSnapshot,
    append_recent_item,
    append_step_history,
    build_page_fingerprint,
    create_initial_state,
    store_memory,
)
from src.agent.step_logger import StepLogger
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
from src.security.classifier import SecurityClassifier
from src.security.gate import SecurityGate
from src.security.security_layer import SecurityLayer
from src.utils.logger import logger

# Max chars for tool result stored in conversation history.
_TOOL_RESULT_CHAR_LIMIT = 500


def _extract_page_text(page_content: str) -> str:
    if not page_content:
        return ""
    marker = "## Page Content (summary)"
    start = page_content.find(marker)
    if start == -1:
        return " ".join(page_content.split())[:RAW_PAGE_TEXT_CHAR_BUDGET]
    text = page_content[start + len(marker) :].strip()
    next_marker = text.find("## Interactive Elements")
    if next_marker != -1:
        text = text[:next_marker]
    return " ".join(text.split())[:RAW_PAGE_TEXT_CHAR_BUDGET]


def _truncate(value: Any, limit: int = 180) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tool_result_json(data: dict[str, Any]) -> str:
    """Serialize a tool result dict, truncated to fit conversation budget."""
    return json.dumps(data, ensure_ascii=False)[:_TOOL_RESULT_CHAR_LIMIT]


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
        security_classifier: SecurityClassifier | None = None,
        security_gate: SecurityGate | None = None,
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
        if security_gate is not None:
            self.security_gate = security_gate
            self.security_classifier = security_classifier or security_gate.classifier
        elif security_classifier is not None:
            self.security_classifier = security_classifier
            self.security_gate = SecurityGate(classifier=security_classifier)
        elif client is not None:
            self.security_classifier = SecurityClassifier(client=client)
            self.security_gate = SecurityGate(classifier=self.security_classifier)
        else:
            self.security_classifier = None
            self.security_gate = None
        self.message_manager = message_manager or MessageManager()
        self.tool_registry = tool_registry or ToolRegistry()
        self.loop_detector = loop_detector or LoopDetector()
        self.step_logger = StepLogger()
        self._step_memory_ops: list[dict[str, str]] = []
        self.state: AgentState | None = None

    # ------------------------------------------------------------------
    # Main orchestration loop
    # ------------------------------------------------------------------

    async def run(self) -> str:
        logger.info(f"Agent run started: task={_truncate(self.task, 200)}")
        state = create_initial_state(self.task)
        self.state = state  # expose for introspection / testing

        if self.client is None:
            message = "OPENAI_API_KEY is not configured, so the browser agent cannot run."
            logger.error(message)
            return message

        # Initial observation → first user message in conversation.
        await self._observe(state, force_visual=False)
        self.message_manager.add_observation(state, state.get("last_screenshot_b64", ""))

        while state.get("step_count", 0) < MAX_STEPS:
            state["step_count"] = state.get("step_count", 0) + 1
            self._step_memory_ops = []
            logger.info(
                f"AGENT_STEP: step={state['step_count']}, url={_truncate(state.get('current_url', ''), 120)}, "
                f"subtask={_truncate(state.get('current_subtask', '') or '(not set)', 120)}"
            )

            # Hard limit: block agent if it keeps revisiting the same page.
            visit_count = self.loop_detector.count_page_visits(
                state.get("recent_page_fingerprints", []),
                state.get("page_fingerprint", ""),
            )
            if visit_count > 3:
                self._set_stuck_hint(
                    state,
                    f"HARD BLOCK: You have visited this exact page {visit_count} times. "
                    f"You clearly have all the information this page can give you. "
                    f"Use save_memory NOW to capture anything useful, then move on.",
                )
                self._inject_runtime_hint_into_last_user_message(state.get("stuck_hint", ""))

            # Build messages from accumulated conversation.
            messages = self.message_manager.build_messages(
                system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
            )
            self.step_logger.log_conversation_history(step=state["step_count"], messages=messages)

            t0 = time.perf_counter()
            output = await self._decide(messages, state)
            step_duration_ms = int((time.perf_counter() - t0) * 1000)

            if output is None:
                if state.get("status") in {"done", "error"}:
                    break
                parse_failure_count = state.get("parse_failure_count", 0) + 1
                state["parse_failure_count"] = parse_failure_count

                if parse_failure_count == 1:
                    state["forced_instruction"] = (
                        "You returned an invalid or empty response. You MUST respond with a valid JSON object. "
                        "Look at the screenshot and choose an action."
                    )
                elif parse_failure_count == 2:
                    state["forced_instruction"] = (
                        "Invalid response twice. Your JSON must have evaluation_previous_goal, memory, "
                        "next_goal, and action fields. Action must contain at least one tool call."
                    )
                elif parse_failure_count == 3:
                    state["forced_instruction"] = (
                        "CRITICAL: Failed to produce valid structured output 3 times. "
                        "Respond with JSON now. If stuck, navigate to a search engine."
                    )
                elif parse_failure_count >= 4:
                    task_query = quote_plus(state.get("task", ""))
                    logger.warning(
                        f"AUTO-RECOVERY: Agent stuck for {parse_failure_count} steps without valid output. "
                        f"Force-navigating to Google search."
                    )
                    await self.browser.navigate(f"https://www.google.com/search?q={task_query}")
                    state["parse_failure_count"] = 0
                    state["forced_instruction"] = (
                        "System auto-navigated to Google search as recovery from stuck state. "
                        "Search for what you need or navigate to a relevant URL."
                    )

                # Retry: replace the last user message with a fresh observation.
                await self._observe(state, force_visual=state.get("retry_count", 0) > 0)
                self._replace_last_observation(state)
                continue

            state["parse_failure_count"] = 0  # reset on success
            token_usage = state.get("last_token_usage", {})  # type: ignore[misc]
            prompt_tokens = int((token_usage or {}).get("prompt", 0))
            completion_tokens = int((token_usage or {}).get("completion", 0))

            # Record assistant output in conversation history.
            self.message_manager.add_agent_output(output)

            # Execute actions and collect results.
            results = await self._execute_actions(state, output)

            # Log the structured step block.
            plan_entry = next(
                (e["value"] for e in state.get("memory", []) if e["key"] == "plan"),
                None,
            )
            tool_calls_info = [
                {
                    "name": a.tool_name,
                    "args": json.dumps(a.arguments, ensure_ascii=False)[:120],
                }
                for a in output.action
            ]
            self.step_logger.log_step(
                step=state.get("step_count", 0),
                max_steps=MAX_STEPS,
                url=state.get("current_url", ""),
                title=state.get("page_title", ""),
                subtask=state.get("current_subtask", ""),
                element_count=len(state.get("interactive_elements", [])),
                plan_value=plan_entry,
                tool_calls_info=tool_calls_info,
                memory_ops=list(self._step_memory_ops),
                loop_warning=state.get("stuck_hint", ""),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                step_duration_ms=step_duration_ms,
                evaluation=output.evaluation_previous_goal,
                reasoning_memory=output.memory,
                next_goal=output.next_goal,
            )

            if state.get("status") in {"done", "error"}:
                break

            # Post-action observation → next user message with results embedded.
            pre_action_fingerprint = state.get("page_fingerprint", "")
            await self._wait_for_post_action_observe(results)
            await self._observe(
                state,
                force_visual=bool(
                    state.get("retry_count", 0) > 0 or state.get("consecutive_stuck_steps", 0) > 0
                ),
            )
            self._reconcile_post_action_results(
                state,
                results,
                previous_fingerprint=pre_action_fingerprint,
            )
            self.message_manager.add_action_results(
                results, state, state.get("last_screenshot_b64", "")
            )

            # Compress conversation if it's getting too large.
            self.message_manager.compress_if_needed(task=self.task)

            # Loop detection (operates on state, not messages).
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
                state["final_report"] = "Agent stopped after repeated invalid or failed actions."
                break

        self.step_logger.log_summary(
            status=state.get("status", "error"),
            steps=state.get("step_count", 0),
        )
        if self.security_gate is not None:
            logger.info(self.security_gate.get_summary())
        if self.security_classifier is not None:
            logger.info(self.security_classifier.summary())

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

    # ------------------------------------------------------------------
    # Observe
    # ------------------------------------------------------------------

    async def _observe(self, state: AgentState, *, force_visual: bool) -> None:
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
        page_state = observation.page_state
        viewport = getattr(page_state, "viewport", None)
        active_modal = getattr(page_state, "active_modal", None)
        viewport_snapshot: ViewportSnapshot | None
        if isinstance(viewport, dict):
            viewport_snapshot = {
                "width": int(viewport.get("width", 0)),
                "height": int(viewport.get("height", 0)),
            }
        elif viewport is not None:
            viewport_snapshot = {
                "width": int(viewport.width),
                "height": int(viewport.height),
            }
        else:
            viewport_snapshot = None
        state["viewport"] = viewport_snapshot

        modal_snapshot: ActiveModalSnapshot | None
        if isinstance(active_modal, dict):
            modal_payload: ActiveModalSnapshot = {
                "kind": str(active_modal.get("kind", "")).strip() or "surface",
                "label": str(active_modal.get("label", "")).strip(),
            }
            modal_bbox = active_modal.get("bbox")
            if isinstance(modal_bbox, dict):
                modal_payload["bbox"] = cast(
                    BBox,
                    {
                        "x": int(modal_bbox.get("x", 0)),
                        "y": int(modal_bbox.get("y", 0)),
                        "width": int(modal_bbox.get("width", 0)),
                        "height": int(modal_bbox.get("height", 0)),
                    },
                )
            modal_snapshot = modal_payload
        elif active_modal is not None:
            modal_snapshot = cast(
                ActiveModalSnapshot,
                {
                    "kind": active_modal.kind,
                    "label": active_modal.label,
                    **(
                        {
                            "bbox": cast(
                                BBox,
                                {
                                    "x": active_modal.bbox.x,
                                    "y": active_modal.bbox.y,
                                    "width": active_modal.bbox.width,
                                    "height": active_modal.bbox.height,
                                },
                            )
                        }
                        if active_modal.bbox is not None
                        else {}
                    ),
                },
            )
        else:
            modal_snapshot = None
        state["active_modal"] = modal_snapshot
        state["last_observation"] = {
            "url": observation.page_state.url,
            "title": observation.page_state.title,
            "content": observation.page_state.content,
            "tab_count": observation.tab_count,
            "viewport": state.get("viewport"),
            "active_modal": state.get("active_modal"),
        }
        state["recent_page_fingerprints"] = append_recent_item(
            state.get("recent_page_fingerprints", []),
            fingerprint,
        )
        if (
            state.get("element_ids_unreliable")
            and state.get("element_ids_unreliable_fingerprint")
            and state.get("element_ids_unreliable_fingerprint") != fingerprint
        ):
            state["element_ids_unreliable"] = False
            state["element_ids_unreliable_fingerprint"] = ""
        state["prompt_injection_warnings"] = self.security_layer.check_prompt_injection(
            observation.page_state
        )
        state["page_text_excerpt"] = _extract_page_text(observation.page_state.content)
        logger.debug(
            f"OBSERVE: title={_truncate(observation.page_state.title, 120)}, "
            f"elements={len(observation.elements)}, fingerprint={fingerprint}"
        )
        self.step_logger.log_observation(
            step=state.get("step_count", 0),
            url=observation.page_state.url,
            title=observation.page_state.title,
            element_count=len(observation.elements),
            fingerprint=fingerprint,
            screenshot_captured=bool(state.get("last_screenshot_b64")),
        )

    # ------------------------------------------------------------------
    # Decide (LLM call)
    # ------------------------------------------------------------------

    async def _decide(
        self,
        messages: list[dict[str, Any]],
        state: AgentState,
    ) -> AgentOutput | None:
        client = self.client
        if client is None:
            self._record_failure(state, action="model_call", result="LLM client is not configured.")
            return None

        # === DEBUG DUMP ===
        step = state.get("step_count", 0)
        dump_dir = "debug_dumps"
        os.makedirs(dump_dir, exist_ok=True)

        dump_messages = []
        for msg in messages:
            dumped = dict(msg)
            content = dumped.get("content", "")
            if isinstance(content, list):
                cleaned_content = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        cleaned_content.append(
                            {"type": "image_url", "image_url": "[SCREENSHOT_REMOVED]"}
                        )
                    else:
                        cleaned_content.append(item)
                dumped["content"] = cleaned_content
            elif isinstance(content, str) and "base64" in content:
                dumped["content"] = content[:200] + "...[BASE64_TRUNCATED]"
            dump_messages.append(dumped)

        dump_path = os.path.join(dump_dir, f"step_{step:03d}_messages.json")
        with open(dump_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
            json.dump(dump_messages, f, ensure_ascii=False, indent=2, default=str)

        summary_path = os.path.join(dump_dir, f"step_{step:03d}_summary.txt")
        with open(summary_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
            f.write(f"=== STEP {step} ===\n")
            f.write(f"URL: {state.get('current_url', '')}\n")
            f.write(f"Subtask: {state.get('current_subtask', '')}\n")
            f.write(f"Memory: {state.get('memory', [])}\n")
            f.write(f"Messages count: {len(messages)}\n\n")
            for i, msg in enumerate(dump_messages):
                role = msg.get("role", "?")
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    text = "\n".join(text_parts)
                    has_image = any(
                        item.get("type") == "image_url"
                        for item in content
                        if isinstance(item, dict)
                    )
                    f.write(
                        f"[{i}] {role} ({len(text)} chars{', +screenshot' if has_image else ''}):\n{text}\n"
                    )
                elif isinstance(content, str):
                    f.write(f"[{i}] {role} ({len(content)} chars):\n")
                    if role == "system" and len(content) > 500:
                        f.write(f"{content[:200]}\n...[TRUNCATED]...\n{content[-200:]}\n")
                    else:
                        f.write(f"{content}\n")
                f.write(f"\n{'=' * 60}\n\n")

        logger.debug(f"DEBUG DUMP: step {step} messages saved to {dump_path}")
        # === END DEBUG DUMP ===

        try:
            response = await client.chat.completions.create(
                model=MAIN_MODEL,
                temperature=TEMPERATURE,
                messages=cast(list[ChatCompletionMessageParam], messages),
                response_format={
                    "type": "json_schema",
                    "json_schema": AgentOutput.to_json_schema(),
                },
            )
        except Exception as exc:
            self._record_failure(
                state,
                action="model_call",
                result=f"LLM call failed: {exc}",
            )
            return None

        raw_content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        state["last_token_usage"] = {"prompt": prompt_tokens, "completion": completion_tokens}  # type: ignore[typeddict-unknown-key]

        try:
            output = AgentOutput.model_validate_json(raw_content)
        except Exception as exc:
            self._record_failure(
                state,
                action="model_call",
                result=f"Failed to parse structured output: {exc}. Raw: {raw_content[:200]}",
            )
            return None

        # Store reasoning fields in state.
        state["last_evaluation"] = output.evaluation_previous_goal
        state["last_reasoning_memory"] = output.memory
        state["last_next_goal"] = output.next_goal

        # Phase-switch data-loss check before updating subtask.
        old_subtask = state.get("current_subtask", "")
        if old_subtask and old_subtask != output.next_goal:
            self._check_phase_switch_data_loss(state, old_subtask, output.next_goal)
        state["current_subtask"] = output.next_goal

        # Dump structured output for debugging.
        with open(summary_path, "a", encoding="utf-8") as f:  # noqa: ASYNC230
            f.write(f"\n=== AGENT OUTPUT ===\n{output.model_dump_json(indent=2)}\n")

        logger.info(
            "AGENT REASONING:\n"
            f"  Eval: {_truncate(output.evaluation_previous_goal, 200)}\n"
            f"  Memory: {_truncate(output.memory, 200)}\n"
            f"  Next: {_truncate(output.next_goal, 200)}\n"
            f"  Actions: {[a.tool_name for a in output.action]}"
        )
        return output

    # ------------------------------------------------------------------
    # Execute actions
    # ------------------------------------------------------------------

    async def _execute_actions(
        self, state: AgentState, output: AgentOutput
    ) -> list[dict[str, Any]]:
        """Execute all actions from AgentOutput. Returns list of result dicts."""
        results: list[dict[str, Any]] = []
        browser_action_executed = False
        step_tool_names = frozenset(a.tool_name for a in output.action)

        for action in output.action:
            name = action.tool_name.strip()
            arguments = action.arguments

            validation_error = self.tool_registry.validate(name, arguments)
            if validation_error:
                self._record_failure(state, action=name, result=validation_error)
                results.append({"tool": name, "success": False, "description": validation_error})
                break

            action_signature = self.tool_registry.render_action_signature(name, arguments)
            logger.info(
                f"EXECUTE: tool={name}, args={_truncate(json.dumps(arguments, ensure_ascii=False), 240)}"
            )

            if self.tool_registry.is_browser_action(name):
                if browser_action_executed:
                    err = "Only one browser action is allowed per step."
                    self._record_failure(state, action=name, result=err)
                    results.append({"tool": name, "success": False, "description": err})
                    break
                block_reason = self._should_block_browser_action(state, name, action_signature)
                if block_reason:
                    err = block_reason
                    results.append({"tool": name, "success": False, "description": err})
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
                state["recent_browser_actions"] = append_recent_item(
                    state.get("recent_browser_actions", []),
                    action_signature,
                )

                results.append(
                    {
                        "tool": name,
                        "success": bool(result.get("success", False)),
                        "description": str(result.get("description", "")),
                        **{k: v for k, v in result.items() if k not in ("success", "description")},
                    }
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
                    state["overlay_click_blocked"] = False
                    state["overlay_blocked_element"] = None
                    if result.get("page_changed", False):
                        state["consecutive_stuck_steps"] = 0
                        state["stuck_hint"] = ""
                    else:
                        self._apply_action_loop_hint(state)
                else:
                    self._record_failure(
                        state,
                        action=name,
                        result=str(
                            result.get("error", result.get("description", "Browser action failed"))
                        ),
                        invalid_tool_call=False,
                    )
                    description = str(result.get("description", "")).lower()
                    error_text = str(result.get("error", "")).lower()
                    if (
                        "overlay" in description
                        or "intercept" in description
                        or "overlay" in error_text
                        or "intercept" in error_text
                    ):
                        state["overlay_click_blocked"] = True
                        element_id = arguments.get("element_id")
                        state["overlay_blocked_element"] = (
                            int(element_id) if element_id is not None else None
                        )
                    elif (
                        result.get("disabled")
                        or "disabled" in description
                        or "not enabled" in error_text
                    ):
                        self._set_stuck_hint(
                            state,
                            "The control you tried to use is disabled or not ready yet. "
                            "Do NOT treat this subtask as complete. Wait for the UI to enable it, "
                            "or take a different action that makes it available.",
                        )
                break

            # State action.
            state["recent_action_signatures"] = append_recent_item(
                state.get("recent_action_signatures", []),
                action_signature,
            )
            tool_result = await self._execute_state_action(name, arguments, state, step_tool_names)
            results.append(
                {
                    "tool": name,
                    "success": not tool_result["data"].get("error"),
                    "description": str(
                        tool_result["data"].get("error") or tool_result["data"].get("status", "ok")
                    ),
                    **{
                        k: v for k, v in tool_result["data"].items() if k not in ("status", "error")
                    },
                }
            )

            if tool_result["stop"]:
                break

        return results

    # ------------------------------------------------------------------
    # State actions
    # ------------------------------------------------------------------

    async def _execute_state_action(
        self,
        name: str,
        arguments: dict[str, Any],
        state: AgentState,
        step_tool_names: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Execute a non-browser tool. Returns {"stop": bool, "data": dict}."""
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
            # Categorize and log the memory operation.
            if key == "plan":
                category = "plan"
            elif "url" in key or "link" in key:
                category = "url_bookmark"
            elif "user_response" in key:
                category = "user_response"
            elif "phase_transition" in state.get("current_subtask", "").lower():
                category = "phase_transition"
            else:
                category = "data_extraction"
            self._step_memory_ops.append({"key": key, "value": value, "category": category})
            self.step_logger.log_memory_operation(
                step=state.get("step_count", 0),
                key=key,
                value=value,
                trigger_category=category,
            )
            return {"stop": False, "data": {"status": "ok", "key": key, "value": value}}

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
            return {"stop": True, "data": {"status": "ok", "answer": answer}}

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
            return {"stop": True, "data": {"status": "done", "summary": summary}}

        self._record_failure(state, action=name, result="Unsupported state tool.")
        return {"stop": True, "data": {"error": "Unsupported state tool."}}

    def _replace_last_observation(self, state: AgentState) -> None:
        """Replace the last user message in conversation with a fresh observation.

        This prevents two consecutive user messages when the model returns
        no tool calls and we need to retry.
        """
        # Pop the last user message (if it exists and is indeed a user message).
        if (
            self.message_manager.conversation
            and self.message_manager.conversation[-1].get("role") == "user"
        ):
            self.message_manager.conversation.pop()
        self.message_manager.add_observation(state, state.get("last_screenshot_b64", ""))

    def _inject_runtime_hint_into_last_user_message(self, hint: str) -> None:
        if not hint or not self.message_manager.conversation:
            return
        last_message = self.message_manager.conversation[-1]
        if last_message.get("role") != "user":
            return

        note = f"\n\n## ⚠ Loop Warning\n{hint}"
        content = last_message.get("content", "")
        if isinstance(content, str):
            if hint not in content:
                last_message["content"] = content + note
            return
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text", ""))
                    if hint not in text:
                        item["text"] = text + note
                    return

    def _find_interactive_element(
        self, state: AgentState, element_id: Any
    ) -> ElementSnapshot | None:
        if not isinstance(element_id, int):
            return None
        for element in state.get("interactive_elements", []):
            if element.get("index", element.get("ref")) == element_id:
                return element
        return None

    async def _execute_browser_action(
        self,
        name: str,
        arguments: dict[str, Any],
        state: AgentState,
    ) -> dict[str, Any]:
        element_info = self._find_interactive_element(state, arguments.get("element_id"))
        if self.security_gate is not None:
            allowed, verdict = await self.security_gate.check(
                action_name=name,
                arguments=arguments,
                element_info=element_info,
                page_url=state.get("current_url", ""),
                page_title=state.get("page_title", ""),
                page_text_excerpt=state.get("page_text_excerpt", ""),
                prompt_injection_warnings=state.get("prompt_injection_warnings", []),
                user_task=state.get("task", ""),
                screenshot_b64=state.get("last_screenshot_b64", ""),
                step=state.get("step_count", 0),
            )
            if not allowed:
                return {
                    "success": False,
                    "description": (
                        f"Action blocked by security: "
                        f"{verdict.reason if verdict else 'user denied'}"
                    ),
                    "error": verdict.reason if verdict else "security gate blocked the action",
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
            if name == "click_coordinates":
                return await self.browser.click_coordinates(
                    int(arguments["x"]),
                    int(arguments["y"]),
                    str(arguments["description"]),
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

    async def _wait_for_post_action_observe(self, results: list[dict[str, Any]]) -> None:
        browser_result = next(
            (
                result
                for result in results
                if self.tool_registry.is_browser_action(str(result.get("tool", "")))
            ),
            None,
        )
        if browser_result is None:
            return
        if (
            browser_result.get("success")
            and browser_result.get("tool") in {"click", "click_coordinates"}
            and browser_result.get("page_changed") is False
        ):
            try:
                await self.browser.wait(0.3)
            except Exception:
                pass

    def _reconcile_post_action_results(
        self,
        state: AgentState,
        results: list[dict[str, Any]],
        *,
        previous_fingerprint: str,
    ) -> None:
        browser_result = next(
            (
                result
                for result in results
                if self.tool_registry.is_browser_action(str(result.get("tool", "")))
            ),
            None,
        )
        if browser_result is None or not browser_result.get("success"):
            return

        tool_name = str(browser_result.get("tool", ""))
        current_fingerprint = state.get("page_fingerprint", "")
        ui_changed = bool(current_fingerprint and current_fingerprint != previous_fingerprint)

        if browser_result.get("page_changed"):
            if current_fingerprint != state.get("element_ids_unreliable_fingerprint", ""):
                state["element_ids_unreliable"] = False
                state["element_ids_unreliable_fingerprint"] = ""
            return

        if ui_changed:
            description = str(browser_result.get("description", "")).strip()
            if "visible UI changed after the action" not in description:
                description = (
                    f"{description}. visible UI changed after the action"
                    if description
                    else "visible UI changed after the action"
                )
            browser_result["description"] = description
            browser_result["page_changed"] = True
            last_action_result = state.get("last_action_result")
            if last_action_result is not None:
                last_action_result["description"] = description
                last_action_result["page_changed"] = True
            state["consecutive_stuck_steps"] = 0
            state["element_ids_unreliable"] = False
            state["element_ids_unreliable_fingerprint"] = ""
            return

        if tool_name == "click":
            message = (
                f"{browser_result.get('description', 'Click action')} had no observable effect on this unchanged page. "
                "Do NOT keep testing adjacent buttons one by one. "
                "The interactive elements list may be unreliable here; use click_coordinates(x, y, description)."
            )
            browser_result["success"] = False
            browser_result["description"] = message
            browser_result["page_changed"] = False
            last_action_result = state.get("last_action_result")
            if last_action_result is not None:
                last_action_result["success"] = False
                last_action_result["description"] = message
                last_action_result["page_changed"] = False
                last_action_result["error"] = message
            state["last_error"] = message
            state["retry_count"] = state.get("retry_count", 0) + 1
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            state["element_ids_unreliable"] = True
            state["element_ids_unreliable_fingerprint"] = current_fingerprint
            self._set_stuck_hint(
                state,
                "This click had no observable effect on the unchanged page. "
                "Do NOT keep testing adjacent buttons one by one. "
                "The interactive elements list may be unreliable here. "
                "Use click_coordinates(x, y, description) guided by the screenshot.",
            )
            return

        if tool_name == "click_coordinates":
            message = (
                f"{browser_result.get('description', 'Coordinate click')} had no observable effect. "
                "Choose a different visible target or change strategy."
            )
            browser_result["success"] = False
            browser_result["description"] = message
            browser_result["page_changed"] = False
            last_action_result = state.get("last_action_result")
            if last_action_result is not None:
                last_action_result["success"] = False
                last_action_result["description"] = message
                last_action_result["page_changed"] = False
                last_action_result["error"] = message
            state["last_error"] = message
            state["retry_count"] = state.get("retry_count", 0) + 1
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1

    def _should_block_browser_action(
        self, state: AgentState, name: str, action_signature: str
    ) -> str | None:
        if (
            name == "click"
            and state.get("element_ids_unreliable")
            and state.get("element_ids_unreliable_fingerprint") == state.get("page_fingerprint")
        ):
            message = (
                "Blocked: the interactive elements list may be unreliable on this unchanged page. "
                "Use click_coordinates(x, y, description) instead of another click(element_id)."
            )
            self._set_stuck_hint(
                state,
                "The interactive elements list may be unreliable on this unchanged page. "
                "Use click_coordinates(x, y, description) instead of another click(element_id).",
            )
            state["last_error"] = message
            state["step_history"] = append_step_history(
                state.get("step_history", []),
                step=state.get("step_count", 0),
                action=action_signature,
                result=message,
                success=False,
            )
            return message
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
            return "Blocked: repeated action on unchanged page."
        return None

    def _apply_action_loop_hint(self, state: AgentState) -> None:
        action_hint = self.loop_detector.detect_action_loop(state.get("recent_browser_actions", []))
        if action_hint:
            self._set_stuck_hint(state, action_hint)
        else:
            state["consecutive_stuck_steps"] = 0
            state["stuck_hint"] = ""

    def _check_phase_switch_data_loss(
        self, state: AgentState, old_subtask: str, new_subtask: str
    ) -> None:
        """Warn if switching subtask on an order-rich page without saving data."""
        current_url = state.get("current_url", "").lower()
        page_title = state.get("page_title", "").lower()
        has_order_context = any(
            kw in current_url or kw in page_title
            for kw in ("order", "заказ", "check", "history", "cart", "корзин")
        )
        memory_keys = {entry["key"] for entry in state.get("memory", [])}
        has_order_data = any(
            kw in key for key in memory_keys for kw in ("order", "item", "product", "товар")
        )
        if has_order_context and not has_order_data:
            state["phase_switch_warning"] = (
                "WARNING: You are switching subtask while on a page that may contain "
                "order/product data, but you haven't saved any items to memory. "
                "Consider using save_memory first."
            )
        self.step_logger.log_phase_transition_warning(
            current_url=state.get("current_url", ""),
            page_title=state.get("page_title", ""),
            old_subtask=old_subtask,
            new_subtask=new_subtask,
            memory_saved_this_step=bool(self._step_memory_ops),
        )

    def _set_stuck_hint(self, state: AgentState, hint: str) -> None:
        state["stuck_hint"] = hint
        current_step = state.get("step_count", 0)
        if state.get("last_stuck_hint_step", 0) != current_step:
            state["consecutive_stuck_steps"] = state.get("consecutive_stuck_steps", 0) + 1
            state["last_stuck_hint_step"] = current_step
        logger.warning(f"LOOP_HINT step={state.get('step_count', 0)}: {hint}")
        self.step_logger.loop_warning_count += 1

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
