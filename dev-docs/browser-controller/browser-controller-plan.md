# Plan: Browser Controller

## Goal
Implement `src/browser/controller.py` — the foundational module that launches a persistent, headed Chromium browser via Playwright and exposes a three-function API: `launch_browser`, `wait_for_page_ready`, and `close_browser`. Update `src/main.py` to wire up the launch/close cycle and verify the module works end-to-end. This is implementation step 2 of the overall project (after project init).

## Current State
- `src/browser/controller.py` (line 1): single stub comment, no implementation
- `src/main.py` (lines 1–9): `asyncio.run(main())` prints "AI Browser Agent starting...", no browser logic
- `playwright>=1.58.0` already declared in `pyproject.toml` and locked in `uv.lock`
- `aioconsole>=0.8.2` already declared (needed for non-blocking input in main.py)

## Proposed Approach

### `src/browser/controller.py`

**Imports:**
```python
import asyncio
from pathlib import Path
from playwright.async_api import AsyncPlaywright, BrowserContext, Download, Page, async_playwright
```

**Function 1 — `launch_browser() -> tuple[AsyncPlaywright, BrowserContext, Page]`:**
1. `Path("./downloads").mkdir(exist_ok=True)` — create dir before handler registration
2. `playwright = await async_playwright().start()`
3. `context = await playwright.chromium.launch_persistent_context(".browser-data", headless=False, viewport={"width": 1280, "height": 900}, locale="ru-RU", args=["--disable-blink-features=AutomationControlled"])`
4. `page = context.pages[0] if context.pages else await context.new_page()`
5. Dialog handler on `page` (not context — dialogs are page-scoped in Playwright):
   `page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))`
6. Download handler on `context` (context-level catches all tabs):
   ```python
   async def _on_download(download: Download) -> None:
       await download.save_as(Path("./downloads") / download.suggested_filename)
   context.on("download", lambda dl: asyncio.ensure_future(_on_download(dl)))
   ```
7. Return `playwright, context, page`

**Function 2 — `wait_for_page_ready(page: Page, timeout: int = 10000) -> None`:**
Three independent levels, each in `try/except Exception: pass`:
- **Level 1:** `await page.wait_for_load_state("domcontentloaded", timeout=timeout)`
- **Level 2:** JS poll every 200ms — `await page.evaluate("document.body ? document.body.innerHTML.length : 0")`, compare to previous; 3 stable consecutive reads = done; hard fallback at 5s
- **Level 3:** `await asyncio.sleep(0.3)` — final buffer for late JS mutations

**Function 3 — `close_browser(context: BrowserContext, playwright: AsyncPlaywright) -> None`:**
`await context.close()` then `await playwright.stop()`

### `src/main.py`
```python
import asyncio
from aioconsole import ainput
from src.browser.controller import close_browser, launch_browser

async def main() -> None:
    print("AI Browser Agent starting...")
    playwright, context, page = await launch_browser()
    print(f"Browser opened. Current URL: {page.url}")
    await ainput("Press Enter to close...")
    await close_browser(context, playwright)

if __name__ == "__main__":
    asyncio.run(main())
```

## Alternatives Considered

**A. Store playwright instance in a module-level global** — avoids changing return signature. Rejected: hidden global state makes testing and multi-instance scenarios harder; explicit DI is safer.

**B. Return a dataclass `BrowserSession`** — cleaner API. Rejected: over-engineering for this stage; the rest of the codebase isn't built yet. Revisit when agent core is wired up.

**C. Use `page.wait_for_load_state("networkidle")`** for wait_for_page_ready — simpler. Rejected: `networkidle` timeouts frequently on pages with polling/analytics; the three-level approach is more robust for real-world SPAs.

## Key Decisions
- **Return `playwright` from `launch_browser`**: `BrowserContext.close()` alone leaves the playwright process running; `playwright.stop()` is required for clean exit. Returning it avoids globals.
- **Dialog handler on `page`, not `context`**: Playwright dialog events are page-scoped; attaching to context doesn't work.
- **Download handler on `context`**: Downloads can originate from any tab/popup; context-level catches all.
- **`aioconsole.ainput` in main.py**: Blocking `input()` freezes the event loop, preventing Playwright's async internals (timeouts, event handlers) from running.

## Risks & Edge Cases
- **`.browser-data/` locked by another process**: If a previous run didn't exit cleanly, Playwright will fail to launch. No workaround needed at this stage — error will surface naturally.
- **`context.pages[0]` may be a special internal page** on some Chromium profiles. If `page.url` returns `chrome://` URLs, `wait_for_page_ready` must not choke — the `try/except` guards handle this.
- **`download.suggested_filename` collisions**: Two downloads with the same name will silently overwrite. Acceptable for now; deduplication is out of scope.
- **`asyncio.get_event_loop()` deprecation** in Python 3.10+: Use `asyncio.get_running_loop()` instead in `wait_for_page_ready`.

## Dependencies
- `playwright>=1.58.0` — `launch_persistent_context`, async API
- `aioconsole>=0.8.2` — `ainput` for non-blocking CLI input
- Both already in `pyproject.toml`

## Definition of Done
- [ ] `uv run python -m src.main` opens a headed Chromium window
- [ ] Terminal prints `Browser opened. Current URL: <url>`
- [ ] Pressing Enter closes browser and exits process cleanly
- [ ] Second run after manual browsing retains cookies/session (`.browser-data/` persisted)
- [x] `uv run ruff check src/` → 0 errors
- [x] `uv run ruff format src/ --check` → 0 errors
- [x] `uv run pyright src/` → 0 errors
