# browser agent

Browser agent with a single orchestration loop:

`observe -> eval -> act -> observe`

## Architecture

- `src/agent/core.py` — main `Agent` runtime
- `src/agent/message_manager.py` — compact prompt/context assembly
- `src/agent/tool_registry.py` — native OpenAI tool-calling schema and validation
- `src/agent/page_summarizer.py` — separate sidecar for page compression
- `src/browser/manager.py` — browser execution
- `src/parser/page_parser.py` — DOM and visible-text extraction

## Commands

```bash
uv run python -m src.main
uv run pytest -q
uv run ruff check .
uv run pyright
```

## Notes

- The runtime no longer uses LangGraph, planning stages, or transition analyzers.
- The page summarizer is advisory only and cannot choose actions or finish tasks.
