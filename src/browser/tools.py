# Browser Tools: execute_tool() — all 13 browser tools implementation
import base64

from playwright.async_api import BrowserContext, Page

from src.browser.controller import wait_for_page_ready
from src.parser.page_parser import PageState, extract_page_state

# Updated by get_page_state, invalidated by navigation-like tools.
# Read by src.security.security_layer via get_last_page_state().
_last_page_state: PageState | None = None


def get_last_page_state() -> PageState | None:
    return _last_page_state


_NAVIGATION_KEYS: frozenset[str] = frozenset(
    {"Enter", "Return", "F5", "Alt+ArrowLeft", "Alt+ArrowRight"}
)


async def execute_tool(
    name: str, args: dict, page: Page, context: BrowserContext
) -> tuple[dict | str, Page]:
    global _last_page_state
    active_page: Page = page

    match name:
        case "navigate":
            try:
                url: str = args["url"]
                if not url.startswith(("http://", "https://")):
                    return {
                        "success": False,
                        "error": f"Unsupported URL scheme: {url!r}. Only http/https allowed.",
                    }, active_page
                _last_page_state = None
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
                _last_page_state = None
                result = await page.go_back(timeout=10000, wait_until="domcontentloaded")
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
                state = await extract_page_state(page)
                _last_page_state = state
                return {"success": True, "content": state.content}, active_page
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Error extracting page state: {e}",
                }, active_page

        case "screenshot":
            try:
                data = await page.screenshot(type="jpeg", quality=75)
                encoded = base64.b64encode(data).decode()
                return {"success": True, "base64_image": encoded}, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "click":
            try:
                ref: int = args["ref"]
                locator = page.locator(f'[data-agent-ref="{ref}"]')
                await locator.wait_for(state="visible", timeout=5000)
                await locator.click()
                _last_page_state = None
                await wait_for_page_ready(page)
                return {
                    "success": True,
                    "description": f"Clicked element [{ref}]",
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case "type_text":
            try:
                ref = args["ref"]
                text: str = args["text"]
                press_enter: bool = args.get("press_enter", False)
                locator = page.locator(f'[data-agent-ref="{ref}"]')
                await locator.wait_for(state="visible", timeout=5000)
                await locator.fill(text)
                _last_page_state = None
                if press_enter:
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
                _last_page_state = None
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
                _last_page_state = None
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
                    _last_page_state = None
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
                _last_page_state = None
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

        case "done":
            try:
                summary: str = args["summary"]
                success: bool = args.get("success", True)
                return {
                    "done": True,
                    "summary": summary,
                    "success": success,
                }, active_page
            except Exception as e:
                return {"success": False, "error": str(e)}, active_page

        case _:
            return {"success": False, "error": f"unknown tool: {name}"}, active_page
