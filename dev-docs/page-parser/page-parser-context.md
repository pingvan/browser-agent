# Context: Page Parser

## Key Files

| File | Role |
|------|------|
| `src/parser/page_parser.py` | **Primary target** — implement everything here |
| `src/main.py` | Update for test: navigate → extract → print |
| `src/browser/controller.py` | Provides `launch_browser()`, `wait_for_page_ready()`, `close_browser()` |
| `CLAUDE.md` — "Page Parser" section | Authoritative spec for output format and architecture |

## Related Files

- `src/browser/tools.py` (stub) — will import `PageState`, `InteractiveElement`, `extract_page_state` in a later task
- `src/agent/core.py` (stub) — will call `extract_page_state(page)` before each LLM step
- `src/agent/context_manager.py` (stub) — will truncate `PageState.content` to fit context window

## Patterns to Follow

- **Async functions**: `async def extract_page_state(page: Page) -> PageState:` — all IO via `await`
- **Dataclasses**: use `@dataclass` from stdlib (no `field()` unless needed); see how `BrowserContext` and `Download` types are imported in `controller.py:4`
- **`page.evaluate()`**: used in `controller.py:44` — returns Python-native types (dict/list/int/str); pass JS as raw string
- **Error handling**: wrap risky calls in `try/except PlaywrightError: pass` — see `controller.py:36–37, 53–54`
- **Type hints everywhere** — project enforces this via pyright basic mode
- **Double quotes, 100-char line limit** — enforced by ruff

## Technical Decisions Log

_Update this as implementation progresses._

- **Single `evaluate()` call per pass** — avoids N round-trips; all DOM work done inside one JS closure
- **`data-agent-ref` mutation** — acceptable side-effect; enables precise element targeting across agent steps
- **`PageState.content` stores pre-formatted string** — avoids re-formatting overhead in hot path

## Useful Commands

```bash
uv run python -m src.main          # run and observe output
uv run ruff check src/ --fix        # lint + auto-fix
uv run ruff format src/             # format
uv run pyright src/                 # type check
```

## Links & References

- Playwright `page.evaluate()` docs: https://playwright.dev/python/docs/api/class-page#page-evaluate
- `document.createTreeWalker` MDN: https://developer.mozilla.org/en-US/docs/Web/API/TreeWalker
- CLAUDE.md "Page parser output format" section — canonical output example
