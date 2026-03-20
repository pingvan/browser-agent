# Tasks: Browser Controller

## Stage 1: Implement `src/browser/controller.py`

- [x] Add imports: `asyncio`, `pathlib.Path`, `playwright.async_api` types (`Playwright`, `BrowserContext`, `Download`, `Page`, `async_playwright`)
- [x] Implement `launch_browser() -> tuple[Playwright, BrowserContext, Page]`:
  - [x] Create `./downloads/` dir with `Path("./downloads").mkdir(exist_ok=True)`
  - [x] Start playwright: `await async_playwright().start()`
  - [x] Call `playwright.chromium.launch_persistent_context()` with correct args (`.browser-data`, headless=False, 1280x900, locale `ru-RU`, `--disable-blink-features=AutomationControlled`)
  - [x] Get or create page: `context.pages[0] if context.pages else await context.new_page()`
  - [x] Register dialog handler on `page` using `asyncio.ensure_future(dialog.accept())`
  - [x] Register download handler on `context` — named async `_on_download`, passed directly
  - [x] Return `playwright, context, page`
- [x] Implement `wait_for_page_ready(page: Page, timeout: int = 10000) -> None`:
  - [x] Level 1: `page.wait_for_load_state("domcontentloaded", timeout=timeout)` in try/except
  - [x] Level 2: JS poll loop — `asyncio.get_running_loop().time()` deadline at +5s, `page.evaluate(...)` for `innerHTML.length`, 200ms sleep, 3-stable-reads break, all in try/except
  - [x] Level 3: `asyncio.sleep(0.3)` in try/except
- [x] Implement `close_browser(context: BrowserContext, playwright: Playwright) -> None`:
  - [x] `await context.close()` then `await playwright.stop()`
- [x] Run `uv run ruff check src/ --fix && uv run ruff format src/` — fix all issues
- [x] Run `uv run pyright src/` — 0 errors

## Stage 2: Update `src/main.py` and verify end-to-end

- [x] Add imports: `aioconsole.ainput`, `src.browser.controller.launch_browser`, `src.browser.controller.close_browser`
- [x] In `main()`: call `launch_browser()`, unpack `playwright, context, page`
- [x] Print `f"Browser opened. Current URL: {page.url}"`
- [x] `await ainput("Press Enter to close...")`
- [x] `await close_browser(context, playwright)`
- [x] Run `uv run ruff check src/ --fix && uv run ruff format src/` — 0 errors
- [x] Run `uv run pyright src/` — 0 errors
- [x] **Manual test 1**: `uv run python -m src.main` → Chromium opens, URL printed, Enter closes cleanly
- [x] **Manual test 2**: navigate to any site manually → press Enter → browser closes
- [ ] **Manual test 3**: run again → cookies/session from previous run are preserved
- [ ] Mark all Definition of Done items in plan as complete
- [ ] Commit: `feat: implement browser controller (launch, wait_for_page_ready, close)`
