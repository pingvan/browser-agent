# Plan: Always Screenshot Architecture

## Goal

Attach a JPEG screenshot to every page-state response so the LLM sees both DOM text
and a visual simultaneously. Currently screenshots are optional, appended as separate
`user` messages after tool results, and require an explicit `screenshot` tool call.
After this change: every `get_page_state` result and every auto-returned page state
from navigation tools includes `screenshot_b64` embedded directly in the `tool` message
as multimodal content. This eliminates ~4 wasted `get_page_state` round-trips per task
and gives the LLM layout/modal awareness it currently lacks.

---

## Current State

### page_parser.py (202 lines)
- `PageState` dataclass (line 21): url, title, content, elements
- `extract_page_state(page)` (line 167): async, returns `PageState` — DOM only, no screenshot

### browser/tools.py (292 lines)
- `_PAGE_CHANGING_TOOLS` (line 17): 7 tools — navigate, click, go_back, select_option, type_text, press_key, switch_tab
- `get_page_state` case (line 105): returns `{"success": True, "content": state.content}` — key is `"content"`, not `"page_state"`
- `screenshot` case (line 116): returns `{"success": True, "base64_image": encoded}` — key is `"base64_image"`
- `execute_tool()` (line 278): appends `page_state` (text only) after page-changing tools

### agent/core.py (182 lines)
- API call (line 38): no `parallel_tool_calls` flag
- Truncation (line 117–120): handles both `content` (get_page_state) and `page_state` keys separately
- Screenshot handling (line 122–128): if `"base64_image" in result`, sends fake tool response + appends image as separate `user` message (line 162–176)
- Screenshot in context: appended as a `user` message (role=user), NOT inside the `tool` message

### agent/context_manager.py (74 lines)
- `_is_screenshot_message()` (line 8): only detects `user`-role messages — won't work for tool-role screenshots
- Drops all old screenshots except the last 1 (entire message deleted)
- `MAX_MESSAGES = 40` (module-level constant, not a class attribute)

---

## Proposed Approach

### Step 1 — page_parser.py: add screenshot infrastructure
- Add `import asyncio`, `import base64`
- Add `PageStateWithScreenshot` dataclass: `page_state: PageState` + `screenshot_b64: str`
- Add `_take_screenshot(page)`: quality=60 JPEG, returns `""` on failure
- Add `extract_page_state_with_screenshot(page)`: runs `asyncio.gather(extract_page_state, _take_screenshot)` for ~200ms speedup

### Step 2 — browser/tools.py: wire screenshots into every page state
- Import `PageStateWithScreenshot, extract_page_state_with_screenshot`
- `get_page_state` case: use new extractor, return `"page_state"` key (rename from `"content"`) + `"screenshot_b64"`
- `screenshot` case: rename `"base64_image"` → `"screenshot_b64"`
- `execute_tool()`: call `extract_page_state_with_screenshot()` for all `_PAGE_CHANGING_TOOLS`, include `screenshot_b64` in result

### Step 3 — agent/core.py: multimodal tool messages
- Add `parallel_tool_calls=False` to API call
- Extract `_build_tool_message()` helper:
  - Pops `screenshot_b64` from result dict
  - Formats text: action metadata + truncated page_state (unified for all tools)
  - Appends injection warning + loop hint to text
  - Returns `{"role": "tool", ..., "content": text}` if no screenshot
  - Returns `{"role": "tool", ..., "content": [text_part, image_url_part]}` if screenshot
- Delete old truncation block (lines 117–120) — now inside `_build_tool_message`
- Delete old `"base64_image"` user-message logic (lines 122–128, 162–176)

### Step 4 — agent/tools_schema.py: update descriptions
- Navigation tools: add "Returns page state and screenshot automatically."
- `get_page_state`: "Call ONLY at task start, after scroll/hover/type_text-without-Enter."
- `screenshot`: "For high-detail inspection only — basic screenshots already come with every page state."

### Step 5 — agent/prompts.py: screenshot usage guidance
- Replace rule 4 ("Use screenshot sparingly") with explanation that screenshots arrive automatically
- Add rule 5: when to call `get_page_state` explicitly

### Step 6 — agent/context_manager.py: prune screenshots correctly
- Add `MAX_SCREENSHOTS_KEPT = 2` class constant
- Rename `_is_screenshot_message()` → `_has_screenshot()` — same logic, works for any role
- Add `_strip_screenshot(msg)`: replaces `image_url` parts with text placeholder (keeps the text parts)
- Update `prepare()`: strip screenshots from all but last `MAX_SCREENSHOTS_KEPT` (don't drop whole messages)

---

## Alternatives Considered

### A. Keep screenshots as separate user messages (current pattern, just add auto-trigger)
- Pro: No changes to context_manager screenshot detection
- Con: Tool result and screenshot are decoupled — LLM may not correlate them correctly; context manager must track paired messages
- **Rejected**: multimodal tool messages are the correct API pattern; image should be co-located with the DOM text it describes

### B. Always use `"high"` detail for screenshots
- Pro: LLM can read fine text from screenshots
- Con: ~800 tokens/screenshot vs ~85 tokens for `"low"`; at 10 steps = 7,150 extra tokens; defeats the efficiency gain
- **Rejected**: DOM provides exact text; `"low"` screenshot is sufficient for layout/modal understanding

### C. Narrow auto-screenshot to 4 navigation tools only (exclude type_text, press_key, switch_tab)
- Pro: Fewer screenshots when typing without navigation
- Con: `type_text` without `press_enter` already gets page_state (current `_PAGE_CHANGING_TOOLS` behavior); inconsistency would require special-casing `execute_tool`
- **Rejected**: keep all 7 tools consistent; slight screenshot overhead for non-Enter typing is acceptable

---

## Key Decisions

- `get_page_state` output key renamed `"content"` → `"page_state"` to unify with navigation tools
- Screenshots embedded in `tool` messages (not `user` messages)
- Screenshot key: `"screenshot_b64"` (replacing `"base64_image"` from standalone `screenshot` tool)
- Old screenshots: strip the image part, keep the text (don't drop entire messages)
- `_PAGE_CHANGING_TOOLS`: keep all 7 existing tools (no narrowing)
- `detail: "low"` for auto-screenshots; standalone `screenshot` tool keeps `quality=75`

---

## Risks & Edge Cases

- **`asyncio.gather` with Playwright**: both `page.evaluate()` and `page.screenshot()` are playwright calls — they may need to be on the same event loop tick; test for any Playwright threading issues
- **Empty `screenshot_b64`**: `_take_screenshot` returns `""` on failure — `_build_tool_message` must handle gracefully (falls back to text-only)
- **`get_page_state` key rename**: any code referencing `result["content"]` must be updated (only `core.py` lines 117–118)
- **`"base64_image"` key rename**: `core.py` checks `"base64_image" in result` — must be updated to `"screenshot_b64"`
- **OpenAI tool message multimodal**: gpt-4.1 supports multimodal content in tool result messages — verify API doesn't reject list content for role=tool
- **Context manager detects wrong role**: `_is_screenshot_message()` currently only checks `user` messages; after migration screenshots are in `tool` messages — update is critical

---

## Dependencies

- `playwright.async_api.Page.screenshot()` — already used in `tools.py:116`
- `asyncio.gather()` — stdlib, no new deps
- `base64` — stdlib, already imported in `tools.py`
- OpenAI gpt-4.1 multimodal tool results — supported by the API

---

## Definition of Done

- [ ] `uv run ruff check src/ && uv run ruff format src/ --check && uv run pyright src/` — all clean
- [ ] `uv run python -m src.main`: simple task (go to example.com) shows tool result with `image_url` part in debug log
- [ ] Navigate/click tool results include both `page_state` text and screenshot in the same `tool` message
- [ ] No `get_page_state` calls after navigate/click in a 5-step task
- [ ] After 5+ navigations: only last 2 screenshots remain in messages (rest stripped to text placeholder)
- [ ] Standalone `screenshot` tool still works (high-quality, embedded in tool message)
