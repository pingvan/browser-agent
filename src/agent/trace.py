import json
from typing import Any

from src.parser.page_parser import InteractiveElement, PageState

_NOISY_RESULT_KEYS: frozenset[str] = frozenset({"page_state", "screenshot_b64"})
_TAG_MAP: dict[str, str] = {
    "a": "link",
    "button": "button",
    "select": "select",
    "textarea": "textarea",
}


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _truncate(value: str, limit: int = 160) -> str:
    compact = _normalize_whitespace(value)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _find_element(page_state: PageState | None, ref: Any) -> InteractiveElement | None:
    if page_state is None or not isinstance(ref, int):
        return None
    for el in page_state.elements:
        if el.ref == ref:
            return el
    return None


def _element_role(el: InteractiveElement) -> str:
    if el.role:
        return el.role
    if el.tag == "input":
        return f"input[{el.input_type or 'text'}]"
    return _TAG_MAP.get(el.tag, el.tag)


def _describe_element(el: InteractiveElement) -> str:
    label = el.aria_label or el.text or el.placeholder or "(no label)"
    parts = [f'[{el.ref}] {_element_role(el)} "{_truncate(label, 100)}"']
    if el.href:
        parts.append(f"→ {_truncate(el.href, 140)}")
    if el.value:
        parts.append(f'value="{_truncate(el.value, 60)}"')
    if el.disabled:
        parts.append("[disabled]")
    return " ".join(parts)


def format_model_note(content: str | None) -> str | None:
    if not content:
        return None
    note = _truncate(content, 240)
    return note or None


def format_tool_arguments(fn_name: str, args: dict[str, Any], page_state: PageState | None) -> str:
    safe_args = dict(args)

    if fn_name == "type_text" and "text" in safe_args:
        text = str(safe_args["text"])
        target = _find_element(page_state, safe_args.get("ref"))
        if target is not None and target.input_type.lower() == "password":
            safe_args["text"] = "[REDACTED]"
        else:
            safe_args["text"] = _truncate(text, 80)

    return json.dumps(safe_args, ensure_ascii=False, sort_keys=True)


def describe_tool_target(
    fn_name: str, args: dict[str, Any], page_state: PageState | None
) -> str | None:
    match fn_name:
        case "click" | "hover" | "select_option" | "type_text":
            ref = args.get("ref")
            element = _find_element(page_state, ref)
            if element is not None:
                return _describe_element(element)
            return f"[{ref}] not found in cached page state"
        case "navigate":
            return _truncate(str(args.get("url", "")), 160)
        case "switch_tab":
            return f"tab #{args.get('index')}"
        case "press_key":
            return _truncate(str(args.get("key", "")), 80)
        case "scroll":
            amount = args.get("amount", 500)
            return f"{args.get('direction')} by {amount}px"
        case "get_page_state":
            return "capture current DOM, interactive elements, and screenshot"
        case "screenshot":
            return "capture screenshot"
        case "get_tabs":
            return "inspect open browser tabs"
        case "go_back":
            return "browser history back navigation"
        case "done":
            return None
        case _:
            return None


def summarize_result(fn_name: str, result: dict[str, Any]) -> str:
    if fn_name == "done":
        success = bool(result.get("success", True))
        return f"task completed (success={success})"

    cleaned = {k: v for k, v in result.items() if k not in _NOISY_RESULT_KEYS}
    if not cleaned:
        return "no structured result"

    if cleaned.get("success") is False and "error" in cleaned:
        return f"failed: {_truncate(str(cleaned['error']), 220)}"

    return _truncate(json.dumps(cleaned, ensure_ascii=False, sort_keys=True), 240)


def format_page_snapshot(page_state: PageState | None) -> str | None:
    if page_state is None:
        return None
    title = _truncate(page_state.title or "(untitled)", 100)
    return f'"{title}" | {page_state.url} | elements={len(page_state.elements)}'


def format_page_transition(
    before_state: PageState | None, after_state: PageState | None
) -> str | None:
    if before_state is None or after_state is None:
        return None
    if before_state.url == after_state.url:
        return None
    return f"{before_state.url} -> {after_state.url}"


def build_step_start_log(
    step: int,
    fn_name: str,
    args: dict[str, Any],
    before_state: PageState | None,
    model_note: str | None,
) -> str:
    lines = [f"Step {step}"]
    if model_note:
        lines.append(f"Model note: {model_note}")
    lines.append(f"Tool: {fn_name}({format_tool_arguments(fn_name, args, before_state)})")
    target = describe_tool_target(fn_name, args, before_state)
    if target:
        lines.append(f"Target: {target}")
    return "\n".join(lines)


def build_step_result_log(
    step: int,
    fn_name: str,
    result: dict[str, Any],
    before_state: PageState | None,
    after_state: PageState | None,
) -> str:
    lines = [f"Step {step} result", f"Result: {summarize_result(fn_name, result)}"]
    if fn_name != "done":
        transition = format_page_transition(before_state, after_state)
        if transition:
            lines.append(f"Transition: {transition}")
        page_snapshot = format_page_snapshot(after_state)
        if page_snapshot:
            lines.append(f"Page: {page_snapshot}")
    return "\n".join(lines)
