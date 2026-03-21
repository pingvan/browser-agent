# Tasks: Always Screenshot

## Stage 1: Screenshot Infrastructure (page_parser + tools)

*Goal: add screenshot extraction to the parser and wire it into all page-state responses in tools.py. After this stage the browser layer is complete; agent layer not yet updated.*

- [ ] `src/parser/page_parser.py` — add `import asyncio`, `import base64` at top
- [ ] `src/parser/page_parser.py` — add `PageStateWithScreenshot` dataclass after `PageState` (fields: `page_state: PageState`, `screenshot_b64: str`)
- [ ] `src/parser/page_parser.py` — add `_take_screenshot(page: Page) -> str` (quality=60, full_page=False, returns `""` on exception)
- [ ] `src/parser/page_parser.py` — add `extract_page_state_with_screenshot(page: Page) -> PageStateWithScreenshot` using `asyncio.gather()`
- [ ] `src/browser/tools.py` — update import from `page_parser` to add `PageStateWithScreenshot, extract_page_state_with_screenshot`
- [ ] `src/browser/tools.py` — update `get_page_state` case: use `extract_page_state_with_screenshot()`, return `"page_state"` key (rename from `"content"`) + `"screenshot_b64"`, store `result_with_ss.page_state` in `_page_states`
- [ ] `src/browser/tools.py` — update `screenshot` case: rename output key `"base64_image"` → `"screenshot_b64"` (keep quality=75)
- [ ] `src/browser/tools.py` — update `execute_tool()`: use `extract_page_state_with_screenshot()`, store `page_state` in cache, add `"screenshot_b64"` to result
- [ ] `uv run ruff check src/ --fix && uv run ruff format src/ && uv run pyright src/` — all clean
- [ ] Commit: `feat: add extract_page_state_with_screenshot, wire screenshots into page state responses`

---

## Stage 2: Multimodal Tool Messages (core.py)

*Goal: agent core sends screenshots embedded in tool messages instead of separate user messages. After this stage the agent correctly sends multimodal content to the LLM.*

- [ ] `src/agent/core.py` — add `parallel_tool_calls=False` to `client.chat.completions.create()` call
- [ ] `src/agent/core.py` — extract `_build_tool_message(tool_call_id, fn_name, result, loop_hint, injection_warning, context_manager)` as a module-level function:
  - Pop `screenshot_b64` from result dict
  - If `"page_state" in result`: format as `action_meta_json + "\n\n" + truncated_page_state`; else `json.dumps(result)`
  - Append injection warning if provided
  - Append loop hint if provided
  - Return plain text `tool` message if no screenshot, multimodal `tool` message if screenshot present
- [ ] `src/agent/core.py` — replace the result-to-message block in the tool_call loop:
  - Compute `injection_warning` (check `"page_state" in result`, use `security_layer.check_prompt_injection(get_last_page_state(page))`)
  - Compute `loop_hint` (`loop_detector.get_unstuck_hint() if loop_detector.is_stuck() else None`)
  - Call `_build_tool_message(...)` and append to messages
- [ ] `src/agent/core.py` — delete old truncation block (lines 117–120 pre-change)
- [ ] `src/agent/core.py` — delete old `"base64_image"` handling: the `if "base64_image" in result` tool_content branch and the user-message append block (lines 122–128, 162–176 pre-change)
- [ ] `uv run ruff check src/ --fix && uv run ruff format src/ && uv run pyright src/` — all clean
- [ ] Manual smoke test: `uv run python -m src.main` → task "go to example.com" → verify debug log shows tool message with `image_url` part (not a separate user message)
- [ ] Commit: `feat: embed screenshots in tool messages, extract _build_tool_message()`

---

## Stage 3: Context Manager + Schema + Prompt Polish

*Goal: context manager correctly prunes tool-role screenshots; tool descriptions and system prompt teach the LLM the new behavior.*

- [ ] `src/agent/context_manager.py` — add `MAX_SCREENSHOTS_KEPT = 2` class constant
- [ ] `src/agent/context_manager.py` — rename `_is_screenshot_message()` → `_has_screenshot()` (same logic; now works for any role since screenshots are in tool messages)
- [ ] `src/agent/context_manager.py` — add `_strip_screenshot(msg)`: replace `image_url` parts in `content` list with `{"type": "text", "text": "[Screenshot removed — outdated. Refer to the latest screenshot above.]"}`; return unchanged if content is not a list
- [ ] `src/agent/context_manager.py` — update `prepare()`: replace old `screenshot_indices[:-1]` drop logic with strip logic that keeps last `MAX_SCREENSHOTS_KEPT` screenshots intact and strips the rest
- [ ] `src/agent/tools_schema.py` — update `navigate` description: add "Returns page state and screenshot automatically — no need to call get_page_state after this."
- [ ] `src/agent/tools_schema.py` — update `click` description: add "Returns updated page state and screenshot automatically."
- [ ] `src/agent/tools_schema.py` — update `type_text` description: add "If press_enter=true, returns updated page state + screenshot."
- [ ] `src/agent/tools_schema.py` — update `select_option`, `go_back`, `switch_tab`, `press_key` descriptions: add "Returns page state and screenshot automatically."
- [ ] `src/agent/tools_schema.py` — update `get_page_state` description: "Call ONLY at task start, after scroll, after hover, or after type_text without Enter. Navigation tools already include page state and screenshot."
- [ ] `src/agent/tools_schema.py` — update `screenshot` description: "High-detail screenshot for reading small text or CAPTCHAs. Basic screenshots already come with every page state."
- [ ] `src/agent/prompts.py` — replace rule 4 with screenshot-usage guidance (DOM gives refs, screenshot gives layout/modals; check screenshot for overlays first)
- [ ] `src/agent/prompts.py` — add rule 5: when to call `get_page_state` explicitly
- [ ] `uv run ruff check src/ --fix && uv run ruff format src/ && uv run pyright src/` — all clean
- [ ] End-to-end test: 5+ navigation steps → confirm only last 2 screenshots in messages, no spurious `get_page_state` calls after navigate/click
- [ ] Commit: `feat: always-screenshot architecture — context pruning, schema and prompt updates`
