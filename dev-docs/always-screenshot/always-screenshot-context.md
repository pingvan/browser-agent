# Context: Always Screenshot

## Key Files

| File | Role in this task |
|------|------------------|
| `src/parser/page_parser.py` | Add `PageStateWithScreenshot`, `_take_screenshot()`, `extract_page_state_with_screenshot()` |
| `src/browser/tools.py` | Wire screenshot into `get_page_state`, `screenshot`, and `execute_tool()` |
| `src/agent/core.py` | Replace per-tool result handling with `_build_tool_message()`; remove old screenshot-as-user-msg |
| `src/agent/context_manager.py` | Update screenshot detection for tool-role messages; strip (not drop) old screenshots |
| `src/agent/tools_schema.py` | Update descriptions to mention auto-screenshot |
| `src/agent/prompts.py` | Add screenshot usage rules, when to call `get_page_state` explicitly |

## Related Files

- `src/browser/controller.py` — `wait_for_page_ready()` used after navigation; no changes needed
- `src/agent/loop_detector.py` — `get_unstuck_hint()` injected into tool text; no changes needed
- `src/security/security_layer.py` — `check_prompt_injection(page_state)` called on page states; no changes needed

## Critical Current Behaviors to Preserve

- `get_last_page_state(page)` in `core.py:93` reads from `_page_states` cache — must still be populated after `get_page_state` and page-changing tools
- `security_layer.check_prompt_injection(updated_state)` takes a `PageState` object (not string) — ensure cache stores `PageState`, not `PageStateWithScreenshot`
- `loop_detector.record_action(fn_name, fn_args)` called after tool execution — unchanged
- `error_recovery.execute_with_retry` wraps `execute_tool` — unchanged
- Messages format: first 2 messages are always system + task (preserved by context_manager)

## Key Line Numbers (current, pre-change)

- `tools.py:17` — `_PAGE_CHANGING_TOOLS` frozenset
- `tools.py:105–114` — `get_page_state` case (returns `"content"` key)
- `tools.py:116–122` — `screenshot` case (returns `"base64_image"` key)
- `tools.py:278–291` — `execute_tool()` wrapper
- `core.py:38–44` — OpenAI API call (add `parallel_tool_calls=False`)
- `core.py:117–120` — truncation block (delete after refactor)
- `core.py:122–128` — `"base64_image"` check (delete after refactor)
- `core.py:162–176` — screenshot appended as `user` message (delete after refactor)
- `context_manager.py:8–12` — `_is_screenshot_message()` (update for tool-role)
- `context_manager.py:20–23` — screenshot drop logic (replace with strip logic)

## Patterns to Follow

- **Dataclasses** for structured data — see `PageState` in `page_parser.py:21`, `InteractiveElement:7`
- **Match/case** for tool dispatch — see `_do_action()` in `tools.py:71`
- **`try/except Exception`** for every Playwright call, return `{"success": False, "error": str(e)}` — see `tools.py:88`, `tools.py:136`
- **`logger.debug()`** for internal diagnostic messages — see `tools.py:289`
- **`ensure_ascii=False`** in `json.dumps` — see `core.py:86`, `core.py:128`
- **`WeakKeyDictionary`** for per-page cache — `tools.py:14`
- **Module-level functions** over classes where possible (page_parser has no class)

## Multimodal Tool Result Format (OpenAI API)

```python
{
    "role": "tool",
    "tool_call_id": "call_abc123",
    "content": [
        {"type": "text", "text": "...DOM and action result..."},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64,/9j/4AAQ...",
                "detail": "low",   # ~85 tokens vs ~800 for "high"
            },
        },
    ],
}
```

## Technical Decisions Log

*(Add decisions here as implementation progresses)*

- **`asyncio.gather` for parallel DOM+screenshot**: saves ~200ms per page state extraction
- **`detail: "low"`**: ~85 tokens vs ~800 for "high"; DOM provides text, screenshot provides layout
- **`"screenshot_b64"` as unified key**: replaces `"base64_image"` from old screenshot tool
- **Strip, don't drop old screenshots**: preserves the text content of old tool results in context
- **Keep all 7 `_PAGE_CHANGING_TOOLS`**: don't narrow scope; uniform behavior is simpler

## Useful Commands

```bash
uv run python -m src.main          # run the agent
uv run ruff check src/ --fix       # lint + autofix
uv run ruff format src/            # format
uv run pyright src/                # type check
```

## Links & References

- Architecture spec: user message at start of planning session (the long markdown doc)
- OpenAI Vision docs: https://platform.openai.com/docs/guides/vision (detail parameter)
- Playwright screenshot: https://playwright.dev/python/docs/api/class-page#page-screenshot
