# Browser Tools: execute_tool() — implementation of all browser tools
import base64
import re
from typing import Any
from weakref import WeakKeyDictionary

from playwright.async_api import BrowserContext, Page

from src.browser.controller import wait_for_page_ready
from src.parser.page_parser import (
    PageState,
    PageStateWithScreenshot,
    extract_page_state_with_screenshot,
    take_screenshot,
)
from src.utils.logger import logger

# Updated by get_page_state, invalidated by navigation-like tools.
# Read by callers via get_last_page_state().
_page_states: WeakKeyDictionary[Page, PageState] = WeakKeyDictionary()


def get_last_page_state(page: Page) -> PageState | None:
    """Return the cached PageState for the given page, or None if stale/missing."""
    return _page_states.get(page)


def _invalidate(page: Page) -> None:
    """Remove cached page state for *page* (marks it as stale)."""
    _page_states.pop(page, None)


_NAVIGATION_KEYS: frozenset[str] = frozenset(
    {"Enter", "Return", "F5", "Alt+ArrowLeft", "Alt+ArrowRight"}
)

# Required args per tool (key = tool name, value = list of required arg names).
_REQUIRED_ARGS: dict[str, list[str]] = {
    "navigate": ["url"],
    "click": ["ref"],
    "type_text": ["ref", "text"],
    "select_option": ["ref", "value"],
    "hover": ["ref"],
    "press_key": ["key"],
    "scroll": ["direction"],
    "switch_tab": ["index"],
    "search_page": ["query"],
    "extract": ["query"],
    "done": ["summary"],
}


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Return an error dict if required args are missing, else None."""
    required = _REQUIRED_ARGS.get(name, [])
    missing = [k for k in required if k not in args]
    if missing:
        return {
            "success": False,
            "error": f"Missing required argument(s) for '{name}': {', '.join(missing)}",
        }
    return None


async def _do_action(
    name: str, args: dict[str, Any], page: Page, context: BrowserContext
) -> tuple[dict[str, Any], Page]:
    active_page: Page = page

    # Validate required args upfront — gives clear errors instead of KeyError.
    if err := _validate_args(name, args):
        return err, active_page

    match name:
        case "navigate":
            try:
                url: str = args["url"]
                if not url.startswith(("http://", "https://")):
                    return {
                        "success": False,
                        "error": f"Unsupported URL scheme: {url!r}. Only http/https allowed.",
                    }, active_page
                _invalidate(page)
                await page.goto(url)
                await wait_for_page_ready(page)
                return {
                    "success": True,
                    "url": page.url,
                    "title": await page.title(),
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "go_back":
            try:
                _invalidate(page)
                result = await page.go_back(timeout=10000)
                if result is None:
                    return {
                        "success": False,
                        "error": "No previous page in history",
                    }, active_page
                await wait_for_page_ready(page)
                return {"success": True, "url": page.url}, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "get_page_state":
            try:
                result_with_ss: PageStateWithScreenshot = await extract_page_state_with_screenshot(
                    page
                )
                _page_states[page] = result_with_ss.page_state
                return {
                    "success": True,
                    "page_state": result_with_ss.page_state.content,
                    "screenshot_b64": result_with_ss.screenshot_b64,
                }, active_page
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Error extracting page state: {e}",
                }, active_page

        case "screenshot":
            try:
                data = await page.screenshot(type="jpeg", quality=85, full_page=False)
                encoded = base64.b64encode(data).decode("utf-8")
                return {"success": True, "screenshot_b64": encoded}, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "click":
            try:
                ref: int = args["ref"]
                locator = page.locator(f'[data-agent-ref="{ref}"]')
                await locator.wait_for(state="visible", timeout=5000)
                await locator.click()
                _invalidate(page)
                await wait_for_page_ready(page)
                return {
                    "success": True,
                    "description": f"Clicked element [{ref}]",
                }, active_page
            except Exception as e:
                error_msg = str(e)
                if "waiting for locator" in error_msg.lower() or "not found" in error_msg.lower():
                    error_msg += (
                        f" Element [{ref}] not found. The page may have changed. "
                        "Call get_page_state() to refresh element refs."
                    )
                return {"success": False, "error": error_msg}, active_page

        case "type_text":
            try:
                ref = args["ref"]
                text: str = args["text"]
                press_enter: bool = args.get("press_enter", False)
                locator = page.locator(f'[data-agent-ref="{ref}"]')
                await locator.wait_for(state="visible", timeout=5000)
                await locator.fill(text)
                if press_enter:
                    _invalidate(page)
                    await page.keyboard.press("Enter")
                    await wait_for_page_ready(page)
                return {
                    "success": True,
                    "description": f"Typed {len(text)} characters into [{ref}]",
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "select_option":
            try:
                ref = args["ref"]
                value: str = args["value"]
                locator = page.locator(f'[data-agent-ref="{ref}"]')
                await locator.wait_for(state="visible", timeout=5000)
                await locator.select_option(value)
                _invalidate(page)
                await wait_for_page_ready(page)
                return {
                    "success": True,
                    "description": f"Selected [{ref}]: {value!r}",
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "hover":
            try:
                ref = args["ref"]
                locator = page.locator(f'[data-agent-ref="{ref}"]')
                await locator.wait_for(state="visible", timeout=5000)
                await locator.hover()
                _invalidate(page)
                return {
                    "success": True,
                    "description": f"Hovered over [{ref}]",
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "press_key":
            try:
                key: str = args["key"]
                await page.keyboard.press(key)
                if key in _NAVIGATION_KEYS:
                    _invalidate(page)
                    await wait_for_page_ready(page)
                return {"success": True}, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "scroll":
            try:
                direction: str = args["direction"]
                if direction not in ("up", "down"):
                    return {
                        "success": False,
                        "error": f"Invalid scroll direction: {direction!r}. Use 'up' or 'down'.",
                    }, active_page
                raw_amount = args.get("amount", 500)
                try:
                    amount = int(raw_amount)
                except (TypeError, ValueError):
                    return {
                        "success": False,
                        "error": f"Invalid scroll amount: {raw_amount!r} (expected integer)",
                    }, active_page
                if amount < 0:
                    amount = abs(amount)
                delta = amount if direction == "down" else -amount
                await page.evaluate("(delta) => window.scrollBy(0, delta)", delta)
                scroll_y: int = await page.evaluate("window.scrollY")
                return {"success": True, "scroll_y": scroll_y}, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "get_tabs":
            try:
                tabs = []
                for i, p in enumerate(context.pages):
                    tabs.append(
                        {
                            "index": i,
                            "url": p.url,
                            "title": await p.title(),
                            "active": p is page,
                        }
                    )
                return {"success": True, "tabs": tabs}, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "switch_tab":
            try:
                index: int = args["index"]
                pages = context.pages
                if index < 0 or index >= len(pages):
                    return {
                        "success": False,
                        "error": f"Tab index {index} out of range",
                    }, active_page
                target = pages[index]
                _invalidate(page)
                await target.bring_to_front()
                active_page = target
                return {
                    "success": True,
                    "index": index,
                    "url": target.url,
                    "title": await target.title(),
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "wait":
            try:
                seconds = min(float(args.get("seconds", 3)), 10.0)
                await page.wait_for_timeout(int(seconds * 1000))
                return {"success": True, "waited_seconds": seconds}, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "search_page":
            try:
                query_str: str = args["query"]
                js = """
                (query) => {
                    const text = document.body.innerText;
                    const results = [];
                    const lower = text.toLowerCase();
                    const q = query.toLowerCase();
                    let pos = 0;
                    while (results.length < 10) {
                        const idx = lower.indexOf(q, pos);
                        if (idx === -1) break;
                        const start = Math.max(0, idx - 50);
                        const end = Math.min(text.length, idx + query.length + 50);
                        results.push({
                            match: text.substring(idx, idx + query.length),
                            context: text.substring(start, end),
                            position: idx
                        });
                        pos = idx + 1;
                    }
                    return results;
                }
                """
                matches: list[dict[str, Any]] = await page.evaluate(js, query_str)
                return {
                    "success": True,
                    "query": query_str,
                    "total_matches": len(matches),
                    "matches": matches,
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "extract":
            try:
                extract_query: str = args["query"]
                js_extract = """
                () => {
                    return document.body.innerText.substring(0, 50000);
                }
                """
                full_text: str = await page.evaluate(js_extract)
                # Simple keyword filtering: return paragraphs containing the query
                paragraphs = re.split(r"\n{2,}", full_text)
                query_lower = extract_query.lower()
                relevant = [p.strip() for p in paragraphs if query_lower in p.lower()]
                if relevant:
                    content = "\n\n".join(relevant[:20])
                else:
                    # No keyword matches — return first 5000 chars as fallback
                    content = full_text[:5000]
                return {
                    "success": True,
                    "query": extract_query,
                    "content": content,
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "done":
            try:
                summary: str = args["summary"]
                success: bool = args.get("success", True)
                files_to_display: list[str] = args.get("files_to_display", [])
                result_dict: dict[str, Any] = {
                    "done": True,
                    "summary": summary,
                    "success": success,
                }
                if files_to_display:
                    result_dict["files_to_display"] = files_to_display
                return result_dict, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case _:
            return {"success": False, "error": f"unknown tool: {name}"}, active_page


# Tools that automatically receive a screenshot after successful execution.
_AUTO_SCREENSHOT_TOOLS: frozenset[str] = frozenset(
    {"navigate", "click", "go_back", "switch_tab", "scroll", "select_option", "press_key", "wait"}
)


async def execute_tool(
    name: str, args: dict[str, Any], page: Page, context: BrowserContext
) -> tuple[dict[str, Any], Page]:
    result, active_page = await _do_action(name, args, page, context)

    if not result.get("success"):
        return result, active_page

    # Auto-screenshot for page-changing tools (vision-first strategy).
    needs_screenshot = name in _AUTO_SCREENSHOT_TOOLS or (
        name == "type_text" and args.get("press_enter")
    )
    if needs_screenshot:
        try:
            screenshot_b64 = await take_screenshot(active_page)
            if screenshot_b64:
                result["screenshot_b64"] = screenshot_b64
        except Exception as e:
            logger.debug(f"execute_tool: screenshot failed for '{name}': {e}")

    return result, active_page
