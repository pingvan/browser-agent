# Fix PR #11 Review Issues — Context

## Key Files

| File | Relevance |
|---|---|
| `src/browser/tools.py:277-290` | `execute_tool` wrapper — Fix 1: add logger.debug to except block |
| `src/browser/controller.py:96-122` | Level 3 spinner wait — Fix 2: tighten selector; Fix 3: trailing whitespace |
| `src/utils/logger.py` | Logger module to import into tools.py |

## Related Files

- `src/agent/core.py:130` — Injection check condition (reviewed, no change needed — `fn_name == "get_page_state"` still covers it)
- `src/browser/tools.py:1-10` — Import block; logger import must be added here

## Patterns to Follow

- **Import order** — `src/browser/tools.py` uses: stdlib → third-party (playwright) → local (src.browser.controller, src.parser.page_parser). Add `from src.utils.logger import logger` at the end of the local imports group.
- **Logger usage** — Other modules (e.g. `src/agent/core.py:75`) use `logger.debug(f"...")`, `logger.error(f"...")`. Match this style.
- **JS selector style** — The existing Level 3 selector in `controller.py:104-106` uses comma-separated CSS classes and `[attr=value]` attribute selectors. Replace only the `[class*="..."]` substring selectors, keep the structure.

## Specific Changes

### Fix 1 — `src/browser/tools.py`

Add import (line ~9, after existing local imports):
```python
from src.utils.logger import logger
```

Replace in `execute_tool` wrapper (line ~287):
```python
# Before
except Exception:
    pass  # best-effort: don't fail the tool if page state extraction fails

# After
except Exception as e:
    logger.debug(f"execute_tool: page state extraction failed for '{name}': {e}")
```

### Fix 2 — `src/browser/controller.py:103-106`

Replace the `querySelectorAll` argument:
```js
// Before
'.spinner, .skeleton, .loading, ' +
'[class*="loader"], [class*="shimmer"], [class*="preloader"], ' +
'[class*="skeleton"], [aria-busy="true"]'

// After
'.spinner, .loader, .skeleton, .loading, .shimmer, .preloader, [aria-busy="true"]'
```

### Fix 3 — `src/browser/controller.py:122`

`        pass ` → `        pass` (trailing space removed — ruff format will also catch this)

## Technical Decisions Log

- Injection check at `core.py:130` is correct as-is: `fn_name == "get_page_state"` handles the explicit call case; `"page_state" in result` handles page-changing tools. No change needed.
- Use `logger.debug`, not `logger.warn`, for the swallowed exception — page state extraction is best-effort; failure is not an operational error.
- Replace substring selectors with exact class names — simpler, avoids false positives from classes like `.file-uploader`, `.carousel-loader-animation`.
- Stage 1 implementation followed the planned approach without deviations; no additional code-path changes were needed beyond the three documented fixes.
- Rename the async function parameter from `timeout` to `load_timeout_ms` to satisfy `ASYNC109` while keeping the Playwright call signature unchanged internally (`timeout=load_timeout_ms`).

## Useful Commands

```bash
uv run ruff check src/ --fix   # lint + autofix
uv run ruff format src/        # format
uv run pyright src/            # type check
```
