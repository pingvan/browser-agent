# Tasks: Fix PR #11 Review Issues

## Stage 1: Apply all three fixes

- [x] `src/browser/tools.py` — add `from src.utils.logger import logger` to local imports (after `src.parser.page_parser` import)
- [x] `src/browser/tools.py:287` — replace bare `except Exception: pass` with `except Exception as e: logger.debug(f"execute_tool: page state extraction failed for '{name}': {e}")`
- [x] `src/browser/controller.py:103-106` — replace `[class*="loader"], [class*="shimmer"], [class*="preloader"], [class*="skeleton"]` substring selectors with exact class names: `.spinner, .loader, .skeleton, .loading, .shimmer, .preloader, [aria-busy="true"]`
- [x] `src/browser/controller.py:122` — remove trailing space after `pass`
- [x] `src/browser/controller.py` — rename async `timeout` parameter in `wait_for_page_ready` to satisfy `ASYNC109`
- [x] `src/browser/controller.py` — remove whitespace-only lines and trailing spaces inside embedded JS strings so `ruff` passes cleanly

## Stage 2: Lint, type-check, commit

- [x] `uv run ruff check src/ --fix` — confirm 0 errors
- [x] `uv run ruff format src/ --check` — confirm no reformatting needed
- [x] `uv run pyright src/` — confirm 0 errors
- [x] Commit with message: `fix: logger in execute_tool wrapper, tighten spinner selector`
