from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    page_fingerprint: str
    recent_page_fingerprints: list[str]
    interactive_elements: list[ElementSnapshot]
    screenshot_b64: str
    transition_summary: str
    cached_page_summary: dict[str, str]

    next_action: dict[str, Any] | None
    planned_actions: list[dict[str, Any]]
    reasoning: str
    last_action: dict[str, Any] | None
    last_action_result: dict[str, Any] | None
    last_action_fingerprint: str
    last_action_signature: str
    needs_transition_analysis: bool

    action_history: list[ActionRecord]
    history_summary: str

    step_count: int
    retry_count: int
    repeated_noop_count: int
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
        page_fingerprint="",
        recent_page_fingerprints=[],
        interactive_elements=[],
        screenshot_b64="",
        transition_summary="",
        cached_page_summary={},
        next_action=None,
        planned_actions=[],
        reasoning="",
        last_action=None,
        last_action_result=None,
        last_action_fingerprint="",
        last_action_signature="",
        needs_transition_analysis=False,
        action_history=[],
        history_summary="",
        step_count=0,
        retry_count=0,
        repeated_noop_count=0,
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


def merge_replanned_plan(
    existing_plan: list[PlanStep] | None, step_descriptions: list[str]
) -> list[PlanStep]:
    completed_steps = [step for step in (existing_plan or []) if step["status"] == "done"]
    completed_descriptions = {step["description"] for step in completed_steps}

    plan: list[PlanStep] = []
    for step in completed_steps:
        plan.append(
            PlanStep(
                id=len(plan),
                description=step["description"],
                status="done",
                result=step["result"],
            )
        )

    for description in step_descriptions:
        text = description.strip()
        if not text or text in completed_descriptions:
            continue
        plan.append(
            PlanStep(
                id=len(plan),
                description=text,
                status="pending",
                result="",
            )
        )

    for step in plan:
        if step["status"] == "pending":
            step["status"] = "active"
            break

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


_TRACKING_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "source",
        "si",
        "spm",
        "yclid",
    }
)


def normalize_url_for_fingerprint(url: str) -> str:
    if not url:
        return ""

    parsed = urlsplit(url)
    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in _TRACKING_QUERY_KEYS:
            continue
        filtered_query.append((key, value))

    normalized_path = parsed.path or "/"
    normalized_query = urlencode(filtered_query, doseq=True)
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            normalized_query,
            "",
        )
    )


def build_page_fingerprint(
    *,
    url: str,
    title: str,
    elements: Sequence[ElementSnapshot],
    tab_count: int,
) -> str:
    payload = {
        "url": normalize_url_for_fingerprint(url),
        "title": title.strip()[:200],
        "tab_count": tab_count,
        "elements": [
            {
                "role": str(element.get("role") or element.get("tag") or "")[:40],
                "label": str(
                    element.get("aria_label")
                    or element.get("text")
                    or element.get("placeholder")
                    or ""
                )[:80],
                "href": normalize_url_for_fingerprint(str(element.get("href", ""))),
                "disabled": bool(element.get("disabled", False)),
            }
            for element in elements[:20]
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()
