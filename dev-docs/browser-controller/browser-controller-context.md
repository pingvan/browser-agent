# Context: Browser Controller

## Key Files

| File | Role |
|------|------|
| `src/browser/controller.py` | **Primary file** — implement `launch_browser`, `wait_for_page_ready`, `close_browser` |
| `src/main.py` | Entry point — update to wire up launch/close and demonstrate the module |

## Related Files (awareness only)

| File | Why relevant |
|------|--------------|
| `src/browser/tools.py` | Will call `wait_for_page_ready` after each navigation — must match its signature |
| `src/browser/__init__.py` | May re-export public API; currently empty |
| `pyproject.toml` | `playwright>=1.58.0` and `aioconsole>=0.8.2` already declared |
| `.gitignore` | `.browser-data/` and `downloads/` are already gitignored |

## Patterns to Follow

- **Async everywhere**: all functions `async def` + `await` (see `src/main.py` existing pattern)
- **Type hints on every signature**: `page: Page`, `timeout: int = 10000`, return type annotations required (pyright basic mode enforces this)
- **f-strings** for string formatting, not `.format()` or `%`
- **`pathlib.Path`** for file paths, not raw strings (project convention, pythonic)
- **Import order**: stdlib → third-party → local, separated by blank lines (ruff `I` rule enforces)
- **Double quotes** everywhere (ruff format enforces)
- **Line length 100** (ruff `E501` is ignored — but keep lines readable)

## Technical Decisions Log

| Decision | Rationale |
|----------|-----------|
| `launch_browser` returns `(playwright, context, page)` | Need `playwright.stop()` for clean exit; avoids module-level global state |
| Dialog handler on `page`, not `context` | Playwright dialog events are page-scoped |
| Download handler on `context` | Context-level catches downloads from all tabs/popups |
| `asyncio.ensure_future` wraps coroutine in event callbacks | Callbacks are sync; `dialog.accept()` is a coroutine |
| `asyncio.get_running_loop()` in `wait_for_page_ready` | `get_event_loop()` deprecated in Python 3.10+ |
| `aioconsole.ainput` in `main.py` | Blocking `input()` freezes asyncio event loop |
| Use `Playwright` (not `AsyncPlaywright`) as the return type | `AsyncPlaywright` doesn't exist in playwright.async_api; `async_playwright().start()` returns `Playwright` |
| `context.on("download", _on_download)` with `# type: ignore[call-overload]` | Playwright Python stubs don't include "download" as a BrowserContext event, but it works at runtime; passing `_on_download` directly (not wrapped in lambda) satisfies Playwright's async callback expectations |
| `asyncio.to_thread(Path.mkdir, ...)` for downloads dir creation | ruff ASYNC240 forbids calling pathlib methods in async functions; `to_thread` offloads to a thread |
| `ASYNC109` added to ruff ignore list | Rule targets trio/anyio timeout patterns; doesn't apply to asyncio code that passes timeout to Playwright's own API |

## Useful Commands

```bash
uv run python -m src.main          # run the agent (test browser launch)
uv run ruff check src/ --fix       # lint + auto-fix
uv run ruff format src/            # format
uv run ruff format src/ --check    # format check (CI)
uv run pyright src/                # type check
```

## Links & References

- [Playwright Python async API](https://playwright.dev/python/docs/api/class-playwright)
- [launch_persistent_context docs](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)
- [Playwright dialogs](https://playwright.dev/python/docs/dialogs)
- [Playwright downloads](https://playwright.dev/python/docs/downloads)
