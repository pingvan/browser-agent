"""action_dispatcher — execute action chains from structured JSON responses.

Parses the ``action`` array from the model response, dispatches each action
to the appropriate handler (browser / file / meta), and implements
page-change detection to stop the chain when navigation occurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import BrowserContext, Page

from src.agent.file_tools import execute_file_tool
from src.browser.tools import execute_tool as execute_browser_tool
from src.utils.logger import logger

# Tools that always change the page — chain stops after execution.
PAGE_CHANGING_TOOLS: frozenset[str] = frozenset({"navigate", "go_back", "switch_tab"})

# Tools that may change the page (links, form submits) — chain stops after.
POTENTIALLY_PAGE_CHANGING_TOOLS: frozenset[str] = frozenset({"click", "select_option", "press_key"})

# Safe to chain — never change the page.
SAFE_CHAIN_TOOLS: frozenset[str] = frozenset(
    {
        "type_text",
        "scroll",
        "search_page",
        "extract",
        "wait",
        "hover",
        "get_page_state",
        "screenshot",
        "get_tabs",
        "write_file",
        "replace_file",
        "read_file",
    }
)

# Browser tools dispatched via execute_browser_tool.
BROWSER_TOOLS: frozenset[str] = frozenset(
    {
        "navigate",
        "click",
        "type_text",
        "select_option",
        "scroll",
        "wait",
        "get_page_state",
        "screenshot",
        "search_page",
        "extract",
        "go_back",
        "get_tabs",
        "switch_tab",
        "press_key",
        "hover",
        "done",
    }
)

FILE_TOOLS: frozenset[str] = frozenset({"write_file", "replace_file", "read_file"})

# Meta-tools handled in core.py, not here.
META_TOOLS: frozenset[str] = frozenset({"create_plan", "update_plan", "ask_human", "done"})


@dataclass
class ActionResult:
    """Result of a single action execution."""

    tool_name: str
    tool_args: dict[str, Any]
    result: dict[str, Any]
    page_changed: bool = False


@dataclass
class ChainResult:
    """Result of executing an entire action chain."""

    executed: list[ActionResult] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    active_page: Page | None = None

    @property
    def last_result(self) -> dict[str, Any] | None:
        return self.executed[-1].result if self.executed else None

    @property
    def has_done(self) -> bool:
        return any(r.tool_name == "done" for r in self.executed)

    @property
    def done_result(self) -> dict[str, Any] | None:
        for r in self.executed:
            if r.tool_name == "done":
                return r.result
        return None


async def dispatch_actions(
    actions: list[dict[str, Any]],
    page: Page,
    context: BrowserContext,
) -> tuple[ChainResult, Page]:
    """Execute a chain of actions, stopping on page-changing tools.

    Returns ``(chain_result, active_page)`` where *active_page* may differ
    from *page* if a tab switch occurred.
    """
    chain = ChainResult(active_page=page)
    active_page = page

    for i, action_dict in enumerate(actions):
        tool_name = next(iter(action_dict))
        tool_args: dict[str, Any] = action_dict[tool_name]
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Meta-tools are returned unexecuted — core.py handles them.
        if tool_name in META_TOOLS:
            chain.executed.append(
                ActionResult(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    result={"_meta": True},
                )
            )
            # done and ask_human always break the chain.
            if tool_name in ("done", "ask_human"):
                chain.skipped = actions[i + 1 :]
                break
            continue

        # File tools — sync execution.
        if tool_name in FILE_TOOLS:
            result = execute_file_tool(tool_name, tool_args)
            chain.executed.append(
                ActionResult(tool_name=tool_name, tool_args=tool_args, result=result)
            )
            continue

        # Browser tools.
        if tool_name in BROWSER_TOOLS:
            result, active_page = await execute_browser_tool(
                tool_name, tool_args, active_page, context
            )
            page_changed = tool_name in PAGE_CHANGING_TOOLS or (
                tool_name in POTENTIALLY_PAGE_CHANGING_TOOLS and bool(result.get("success"))
            )
            chain.executed.append(
                ActionResult(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    result=result,
                    page_changed=page_changed,
                )
            )
            chain.active_page = active_page

            # Stop chain after page-changing action.
            if page_changed and i < len(actions) - 1:
                chain.skipped = actions[i + 1 :]
                logger.debug(
                    f"Chain stopped after page-changing '{tool_name}', "
                    f"{len(chain.skipped)} action(s) skipped."
                )
                break
            continue

        # Unknown tool.
        chain.executed.append(
            ActionResult(
                tool_name=tool_name,
                tool_args=tool_args,
                result={"success": False, "error": f"Unknown tool: {tool_name}"},
            )
        )

    return chain, active_page


def format_chain_result(chain: ChainResult) -> str:
    """Format the chain result as text for the model's next context."""
    parts: list[str] = []

    for ar in chain.executed:
        if ar.result.get("_meta"):
            continue

        # Extract page_state and screenshot separately.
        display = dict(ar.result)
        page_state = display.pop("page_state", None)
        display.pop("screenshot_b64", None)

        header = f"[{ar.tool_name}] "
        if ar.tool_args:
            arg_summary = ", ".join(f"{k}={_short(v)}" for k, v in ar.tool_args.items())
            header += arg_summary

        import json

        parts.append(f"{header}\n{json.dumps(display, ensure_ascii=False)}")

        if page_state:
            parts.append(str(page_state))

    if chain.skipped:
        skipped_names = [next(iter(a)) for a in chain.skipped]
        parts.append(f"[Skipped actions (page changed): {', '.join(skipped_names)}]")

    return "\n\n".join(parts)


def get_chain_screenshot(chain: ChainResult) -> str | None:
    """Return the last screenshot_b64 from the chain, if any."""
    for ar in reversed(chain.executed):
        b64 = ar.result.get("screenshot_b64")
        if b64:
            return str(b64)
    return None


def get_chain_page_state(chain: ChainResult) -> str | None:
    """Return the last page_state from the chain, if any."""
    for ar in reversed(chain.executed):
        ps = ar.result.get("page_state")
        if ps:
            return str(ps)
    return None


def _short(v: Any) -> str:
    """Shorten a value for display."""
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."
