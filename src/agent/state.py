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

AgentStatus = Literal["running", "done", "need_input", "error"]


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


class MemoryEntry(TypedDict):
    key: str
    value: str
    source: str


class StepRecord(TypedDict, total=False):
    step: int
    action: str
    result: str
    success: bool
    page_changed: bool


class StepPacket(TypedDict, total=False):
    step_eval: Literal["success", "partial", "blocked", "failed"]
    decision_note: str
    memory_candidate: str
    next_goal: str


class InspectionCandidate(TypedDict):
    element_id: int
    reason: str


class InspectionResult(TypedDict, total=False):
    question: str
    answer: str
    observations: list[str]
    candidate_elements: list[InspectionCandidate]
    source: Literal["dom", "vision"]
    fingerprint: str


class PageSummary(TypedDict, total=False):
    summary: str
    salient_facts: list[str]


class AgentState(TypedDict, total=False):
    task: str
    current_subtask: str
    status: AgentStatus

    memory: list[MemoryEntry]
    step_history: list[StepRecord]
    last_step_packet: StepPacket
    last_dom_inspection: InspectionResult

    current_url: str
    page_title: str
    page_content: str
    page_fingerprint: str
    page_text_excerpt: str
    last_screenshot_b64: str
    recent_page_fingerprints: list[str]
    interactive_elements: list[ElementSnapshot]
    last_observation: dict[str, Any]

    last_action_result: dict[str, Any] | None
    last_action_signature: str
    last_action_fingerprint: str
    recent_action_signatures: list[str]

    retry_count: int
    invalid_tool_calls: int
    consecutive_failures: int
    consecutive_stuck_steps: int
    last_error: str
    stuck_hint: str
    prompt_injection_warnings: list[str]

    user_response: str | None
    final_report: str
    step_count: int


def create_initial_state(task: str) -> AgentState:
    return AgentState(
        task=task,
        current_subtask="",
        status="running",
        memory=[],
        step_history=[],
        last_step_packet=StepPacket(
            step_eval="partial",
            decision_note="",
            memory_candidate="",
            next_goal="",
        ),
        last_dom_inspection=InspectionResult(
            question="",
            answer="",
            observations=[],
            candidate_elements=[],
            source="dom",
            fingerprint="",
        ),
        current_url="",
        page_title="",
        page_content="",
        page_fingerprint="",
        page_text_excerpt="",
        last_screenshot_b64="",
        recent_page_fingerprints=[],
        interactive_elements=[],
        last_observation={},
        last_action_result=None,
        last_action_signature="",
        last_action_fingerprint="",
        recent_action_signatures=[],
        retry_count=0,
        invalid_tool_calls=0,
        consecutive_failures=0,
        consecutive_stuck_steps=0,
        last_error="",
        stuck_hint="",
        prompt_injection_warnings=[],
        user_response=None,
        final_report="",
        step_count=0,
    )


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


def append_step_history(
    history: list[StepRecord],
    *,
    step: int,
    action: str,
    result: str,
    success: bool,
    page_changed: bool = False,
) -> list[StepRecord]:
    updated = list(history)
    updated.append(
        StepRecord(
            step=step,
            action=action,
            result=result[:MAX_MEMORY_VALUE_LENGTH],
            success=success,
            page_changed=page_changed,
        )
    )
    return updated


def render_memory(memory: list[MemoryEntry]) -> str:
    if not memory:
        return "(memory is empty)"
    return "\n".join(f'• {entry["key"]}: "{entry["value"]}" ({entry["source"]})' for entry in memory)


def render_recent_history(history: list[StepRecord]) -> str:
    if not history:
        return "(no actions yet)"

    recent = history[-ACTION_HISTORY_WINDOW:]
    lines = []
    for entry in recent:
        verdict = "✓" if entry.get("success", False) else "✗"
        changed = " changed" if entry.get("page_changed", False) else ""
        lines.append(
            f"[{verdict}] step {entry.get('step', '?')}: {entry.get('action', '')}{changed} -> {entry.get('result', '')}"
        )
    return "\n".join(lines)


def append_recent_item(items: list[str], value: str, *, limit: int = 8) -> list[str]:
    updated = [item for item in items if item]
    if value:
        updated.append(value)
    return updated[-limit:]


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
