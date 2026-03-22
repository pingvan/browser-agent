from __future__ import annotations

from typing import Any, Literal, TypedDict

from src.config.settings import (
    ACTION_HISTORY_WINDOW,
    MAX_MEMORY_ENTRIES,
    MAX_MEMORY_VALUE_LENGTH,
)

PlanStatus = Literal["pending", "active", "done", "skipped"]
AgentStatus = Literal["running", "done", "need_input", "error", "replan"]


class BBox(TypedDict):
    x: int
    y: int
    width: int
    height: int


class ElementSnapshot(TypedDict, total=False):
    index: int
    ref: int
    tag: str
    role: str
    text: str
    aria_label: str
    placeholder: str
    href: str
    input_type: str
    value: str
    disabled: bool
    bbox: BBox


class PlanStep(TypedDict):
    id: int
    description: str
    status: PlanStatus
    result: str


class MemoryEntry(TypedDict):
    key: str
    value: str
    source: str


class ActionRecord(TypedDict, total=False):
    step: int
    action: str
    result: str
    success: bool


class AgentState(TypedDict, total=False):
    task: str
    status: AgentStatus

    plan: list[PlanStep]
    current_plan_step: int
    plan_reasoning: str

    memory: list[MemoryEntry]

    current_url: str
    page_title: str
    page_summary: str
    interactive_elements: list[ElementSnapshot]
    screenshot_b64: str

    next_action: dict[str, Any] | None
    planned_actions: list[dict[str, Any]]
    reasoning: str

    action_history: list[ActionRecord]
    history_summary: str

    step_count: int
    retry_count: int
    steps_since_last_plan_progress: int

    user_response: str | None
    final_report: str

    last_error: str


def create_initial_state(task: str) -> AgentState:
    return AgentState(
        task=task,
        status="running",
        plan=[],
        current_plan_step=-1,
        plan_reasoning="",
        memory=[],
        current_url="",
        page_title="",
        page_summary="",
        interactive_elements=[],
        screenshot_b64="",
        next_action=None,
        planned_actions=[],
        reasoning="",
        action_history=[],
        history_summary="",
        step_count=0,
        retry_count=0,
        steps_since_last_plan_progress=0,
        user_response=None,
        final_report="",
        last_error="",
    )


def normalize_plan(step_descriptions: list[str]) -> list[PlanStep]:
    plan: list[PlanStep] = []
    for index, description in enumerate(step_descriptions):
        plan.append(
            PlanStep(
                id=index,
                description=description.strip(),
                status="pending",
                result="",
            )
        )
    if plan:
        plan[0]["status"] = "active"
    return plan


def current_plan_step_id(plan: list[PlanStep]) -> int:
    for index, step in enumerate(plan):
        if step["status"] == "active":
            return index
    for index, step in enumerate(plan):
        if step["status"] == "pending":
            return index
    return -1


def mark_plan_step_done(
    plan: list[PlanStep], step_id: int, result: str
) -> tuple[list[PlanStep], int, bool]:
    if step_id < 0 or step_id >= len(plan):
        return plan, current_plan_step_id(plan), False

    updated = [PlanStep(**step) for step in plan]
    for step in updated:
        if step["status"] == "active":
            step["status"] = "pending"

    updated[step_id]["status"] = "done"
    updated[step_id]["result"] = result[:MAX_MEMORY_VALUE_LENGTH]

    next_index = -1
    for index, step in enumerate(updated):
        if step["status"] == "pending":
            step["status"] = "active"
            next_index = index
            break

    return updated, next_index, True


def store_memory(
    memory: list[MemoryEntry],
    *,
    key: str,
    value: str,
    source: str,
) -> list[MemoryEntry]:
    trimmed_value = value.strip()[:MAX_MEMORY_VALUE_LENGTH]
    if not key.strip() or not trimmed_value:
        return memory

    updated = [entry for entry in memory if entry["key"] != key]
    updated.append(MemoryEntry(key=key.strip(), value=trimmed_value, source=source.strip()))
    if len(updated) > MAX_MEMORY_ENTRIES:
        updated = updated[-MAX_MEMORY_ENTRIES:]
    return updated


def refresh_history_summary(action_history: list[ActionRecord]) -> str:
    if len(action_history) <= ACTION_HISTORY_WINDOW:
        return ""

    older = action_history[:-ACTION_HISTORY_WINDOW]
    if not older:
        return ""

    fragments = []
    for entry in older[-5:]:
        fragments.append(
            f"step {entry.get('step', '?')}: {entry.get('action', '')} -> {entry.get('result', '')}"
        )
    return " | ".join(fragments)


def render_plan(plan: list[PlanStep], current_step: int) -> str:
    if not plan:
        return "(plan is empty)"

    lines: list[str] = []
    for index, step in enumerate(plan):
        if step["status"] == "done":
            prefix = "[✓]"
        elif index == current_step or step["status"] == "active":
            prefix = "[→]"
        elif step["status"] == "skipped":
            prefix = "[-]"
        else:
            prefix = "[ ]"

        result = f" — {step['result']}" if step["result"] else ""
        lines.append(f"{prefix} {step['id']}. {step['description']}{result}")
    return "\n".join(lines)


def render_memory(memory: list[MemoryEntry]) -> str:
    if not memory:
        return "(memory is empty)"
    return "\n".join(f'• {entry["key"]}: "{entry["value"]}" ({entry["source"]})' for entry in memory)


def render_recent_history(action_history: list[ActionRecord]) -> str:
    if not action_history:
        return "(no actions yet)"

    recent = action_history[-ACTION_HISTORY_WINDOW:]
    lines = []
    for entry in recent:
        verdict = "✓" if entry.get("success", False) else "✗"
        lines.append(
            f"[{verdict}] step {entry.get('step', '?')}: {entry.get('action', '')} -> {entry.get('result', '')}"
        )
    return "\n".join(lines)
