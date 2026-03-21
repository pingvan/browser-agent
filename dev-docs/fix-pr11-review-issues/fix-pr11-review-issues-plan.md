# Fix PR #11 Review Issues — Plan

## Goal

Fix three concrete issues identified in the code review of PR #11 (`optimization/get-page`):
1. Silent exception swallowing in the `execute_tool` wrapper makes failures invisible during debugging.
2. The overly broad `[class*="loader"]` substring selector in `wait_for_page_ready` can match unrelated elements (e.g. `.file-uploader`, `.carousel`) and stall the agent for up to 1500ms unnecessarily.
3. Trailing whitespace after `pass` on `controller.py:122` (ruff format will flag it).

The injection-check regression flagged in the review turned out to be a false alarm: `fn_name == "get_page_state"` on line 130 of `core.py` still covers that case explicitly.

## Current State

### Issue 1 — Silent failure in `execute_tool` wrapper
**File:** `src/browser/tools.py:277-290`

```python
async def execute_tool(...):
    result, active_page = await _do_action(name, args, page, context)
    if name in _PAGE_CHANGING_TOOLS and result.get("success"):
        try:
            state = await extract_page_state(active_page)
            _page_states[active_page] = state
            result["page_state"] = state.content
        except Exception:
            pass  # best-effort: don't fail the tool if page state extraction fails
    return result, active_page
```

Any exception from `extract_page_state` is silently dropped. There is no log output — the agent just returns a result without `page_state`, and neither the developer nor the LLM knows why.

### Issue 2 — Overly broad spinner selector
**File:** `src/browser/controller.py:103-106`

```python
const loaders = document.querySelectorAll(
    '.spinner, .skeleton, .loading, ' +
    '[class*="loader"], [class*="shimmer"], [class*="preloader"], ' +
    '[class*="skeleton"], [aria-busy="true"]'
);
```

`[class*="loader"]` is a substring match that captures any element whose class attribute contains "loader" — e.g. `.file-uploader`, `.image-preloader-wrapper`, `.carousel-loader-animation`. If any such element is visible on page, Level 3 waits the full 1500ms before giving up.

### Issue 3 — Trailing whitespace
**File:** `src/browser/controller.py:122`

`        pass ` — trailing space after `pass` in the Level 3 except block.

## Proposed Approach

### Fix 1: Log the swallowed exception
Replace `pass` with `logger.debug(...)` in the `execute_tool` except block. Use DEBUG level — it's a best-effort operation and not an error in normal flow. The message should include the tool name and the exception, so failures can be traced in debug output.

### Fix 2: Tighten the spinner selector
Remove the broad substring selectors (`[class*="loader"]`, `[class*="preloader"]`, `[class*="skeleton"]`) and replace them with exact class selectors. Keep `[aria-busy="true"]` — it's a semantic attribute that precisely means "loading". The revised list:

```
'.spinner, .loader, .skeleton, .loading, .shimmer, .preloader, [aria-busy="true"]'
```

This covers the common exact class names while eliminating false positives from substring matching.

### Fix 3: Remove trailing whitespace
Remove the trailing space on line 122. Ruff format handles this automatically — running `uv run ruff format src/` at the end of Stage 1 will fix it.

## Alternatives Considered

**Fix 2 — Keep substring selectors but add exclusions:**
Could add `:not([class*="uploader"]):not([class*="carousel"])` etc., but this is a whack-a-mole approach. Exact class names are simpler and more predictable.

**Fix 2 — Drop Level 3 entirely:**
Level 3 adds value for sites that show spinners post-load (e.g. SPAs fetching data). The fix is worthwhile to keep the feature but make it safer.

**Fix 1 — Use WARN instead of DEBUG:**
Page state extraction failures during normal tool execution are not errors — the tool succeeded, page state is just unavailable. DEBUG is correct. WARN would add noise.

## Key Decisions

- Keep Level 3 (spinner wait) — it provides real value for SPAs; just make the selector precise.
- Use exact class names, not substrings, for spinner detection.
- Log at DEBUG for the best-effort page state failure, not WARN/ERROR.
- The injection-check concern from the review is a false alarm — no change needed in `core.py`.

## Risks & Edge Cases

- **Fix 2 edge case:** Sites that use non-standard spinner class names (e.g. `.lds-ring`, `.spin-overlay`) won't be matched — but they were never matched before the PR either. The risk of regressing spinner detection is low.
- **Fix 1 edge case:** In debug mode, failed page state extractions will now be visible in the log. This is the desired behavior.

## Implementation Update

While running Stage 2 checks, `ruff` surfaced pre-existing lint violations in `src/browser/controller.py` that were not part of the original review notes:

- `ASYNC109` on the async function parameter named `timeout`
- whitespace-only lines inside the embedded JavaScript string
- one remaining trailing space inside the Level 3 visibility check

The implementation scope is extended slightly to fix these code issues directly so Stage 2 can still reach the documented Definition of Done without changing lint configuration.

## Dependencies

- `src/utils/logger.py` — `logger` is **not** currently imported in `tools.py`. Must add `from src.utils.logger import logger` to the import block (stdlib → third-party → local, after the existing local import of `page_parser`).

## Definition of Done

- `tools.py`: `except Exception: pass` replaced with `except Exception as e: logger.debug(...)`.
- `controller.py`: substring selectors removed, exact class list used, trailing whitespace gone.
- `uv run ruff check src/` → 0 errors.
- `uv run ruff format src/ --check` → no reformatting needed.
- `uv run pyright src/` → 0 errors.
