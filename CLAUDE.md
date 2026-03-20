# AI Browser Agent — Project Guide

## Project overview

AI-агент, который автономно управляет видимым веб-браузером для выполнения сложных многошаговых задач. Пользователь вводит задачу текстом в CLI, агент исследует страницу, кликает, вводит текст, навигирует — и сообщает результат.

## Tech stack

- **Language:** Python 3.12+, async/await, type hints everywhere
- **Browser automation:** Playwright for Python (`playwright.async_api`), persistent context, headed Chromium
- **AI provider:** OpenAI GPT-4.1 (`gpt-4.1`) via `openai` SDK, tool use / function calling
- **CLI:** `aioconsole` (async input) + `colorama` (colored output)
- **Package manager:** `uv` — `pyproject.toml` + `uv.lock` (no requirements.txt, no pip)
- **Linter/formatter:** Ruff (replaces flake8 + black + isort)
- **Type checker:** pyright (basic mode)
- **CI:** GitHub Actions — ruff lint + ruff format check + pyright

## Commands

```bash
uv run python -m src.main          # запуск агента
uv run ruff check src/              # линтинг
uv run ruff check src/ --fix        # автоисправление
uv run ruff format src/             # форматирование
uv run ruff format src/ --check     # проверка форматирования (CI)
uv run pyright src/                 # type checking
uv add <package>                    # добавить runtime-зависимость
uv add --dev <package>              # добавить dev-зависимость
```

## Architecture

```
User CLI → Agent Core (LLM loop) → Tool Router → Browser Controller → Playwright → Chromium
                ↑                                        ↓
          Context Manager                          Page Parser (DOM extraction)
          Security Layer                           Error Recovery
          Loop Detector
```

### Core modules

1. **Browser Controller** (`src/browser/controller.py`) — launches persistent Chromium context via `launch_persistent_context()`, handles dialogs/downloads/new tabs, implements `wait_for_page_ready()` (3-level: domcontentloaded → DOM stability → fallback pause)
2. **Page Parser** (`src/parser/page_parser.py`) — extracts interactive elements via `page.evaluate()` JS injection, assigns `[data-agent-ref=N]` attributes, formats compact text representation for LLM (~8K tokens max)
3. **Browser Tools** (`src/browser/tools.py`) — implements 13 tools: navigate, click, type_text, select_option, scroll, get_page_state, screenshot, go_back, get_tabs, switch_tab, press_key, hover, done
4. **Agent Core** (`src/agent/core.py`) — main LLM loop: observe page → call GPT-4.1 with tools → execute tool calls → repeat until `done` or MAX_STEPS=50
5. **Tools Schema** (`src/agent/tools_schema.py`) — OpenAI function calling definitions (JSON Schema)
6. **System Prompt** (`src/agent/prompts.py`) — agent instructions
7. **Context Manager** (`src/agent/context_manager.py`) — sliding window (40 messages), preserves system prompt + task + recent actions
8. **Loop Detector** (`src/agent/loop_detector.py`) — detects ABAB or AAA patterns, injects hint
9. **Security Layer** (`src/security/security_layer.py`) — regex-based detection of dangerous actions (purchase, delete, send, account changes), async CLI confirmation
10. **Error Recovery** (`src/utils/error_recovery.py`) — retry with max 3 attempts, converts exceptions to LLM-readable feedback
11. **CLI** (`src/cli.py`) — async readline loop, colored step output, exit/help commands
12. **Logger** (`src/utils/logger.py`) — colorama-based, levels: DEBUG/INFO/WARN/ERROR

### Key design decisions

- **No hardcoded selectors.** Agent discovers elements dynamically via page parser
- **No predefined action sequences.** Agent decides each step via LLM reasoning
- **Ref-based element targeting.** Parser assigns `data-agent-ref=N` to each interactive element; agent references by `[N]`
- **Errors become feedback.** Exceptions are caught and returned as tool results so LLM can adapt
- **Hybrid page extraction.** Primary: DOM interactive elements + visible text. Fallback: screenshot + vision
- **Sequential tool execution.** Even if GPT-4.1 returns parallel tool_calls, execute them sequentially (browser state is sequential)

## File structure

```
ai-browser-agent/
├── .github/
│   └── workflows/
│       └── ci.yml                   # CI: ruff lint + format check + pyright type check
├── src/
│   ├── __init__.py
│   ├── main.py                     # Entry point: asyncio.run(), launches CLI + browser + agent
│   ├── cli.py                      # Async readline loop, colored output, exit/help
│   ├── browser/
│   │   ├── __init__.py
│   │   ├── controller.py           # launch_browser(), wait_for_page_ready(), event handlers
│   │   └── tools.py                # execute_tool() — all 13 browser tools implementation
│   ├── parser/
│   │   ├── __init__.py
│   │   └── page_parser.py          # extract_page_state(), format_page_state(), JS injections
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── core.py                 # run_agent() — main LLM loop
│   │   ├── prompts.py              # SYSTEM_PROMPT
│   │   ├── tools_schema.py         # OpenAI tools definitions
│   │   ├── context_manager.py      # Sliding window, page state truncation
│   │   └── loop_detector.py        # Cycle detection (ABAB, AAA patterns)
│   ├── security/
│   │   ├── __init__.py
│   │   └── security_layer.py       # is_dangerous(), request_confirmation(), regex patterns
│   └── utils/
│       ├── __init__.py
│       ├── error_recovery.py       # execute_with_retry(), error classification
│       └── logger.py               # Colored logger (colorama)
├── .browser-data/                   # Persistent Chromium profile (gitignored)
├── downloads/                       # Downloaded files (gitignored)
├── pyproject.toml                   # Dependencies, ruff/pyright config, project metadata
├── uv.lock                          # Lockfile with exact versions (committed to git)
├── .env.example                     # OPENAI_API_KEY=sk-...
├── .gitignore                       # .browser-data/, downloads/, __pycache__/, .env, .venv/
└── README.md
```

## Code style

- All async functions use `async def` + `await`
- Type hints on every function signature
- Dataclasses for structured data (PageState, InteractiveElement, SecurityRule)
- No classes where a simple function suffices
- f-strings for string formatting
- `json.dumps(x, ensure_ascii=False)` for Russian text
- Imports grouped: stdlib → third-party → local (enforced by ruff `I` rule)
- Line length: 100 (enforced by ruff)
- Quote style: double quotes (enforced by ruff format)
- Target Python version: 3.12 (enables modern syntax via ruff `UP` rule)

## Tool execution flow

```
LLM returns tool_calls →
  for each tool_call (sequential):
    1. Parse fn_name + fn_args from tool_call
    2. security_layer.is_dangerous(fn_name, fn_args, page_state) → if True: ask user confirmation
    3. error_recovery.execute_with_retry(execute_tool(fn_name, fn_args, page, context))
    4. If fn_name == "done" → return summary, exit loop
    5. Append tool result to messages
    6. loop_detector.record_action() → if stuck: append hint to result
```

## Page parser output format

```
## Current Page
URL: https://example.com
Title: Example

## Page Content (summary)
Visible text from the page, truncated to ~2000 chars...

## Interactive Elements
[0] link "Home" → https://example.com/
[1] input[text] "Search" value=""
[2] button "Submit"
[3] link "About" → https://example.com/about
```

## Implementation order

1. Project init (uv init, pyproject.toml, .env, .gitignore, ci.yml)
2. Browser Controller (launch + wait_for_page_ready)
3. Page Parser (DOM extraction + formatting)
4. Browser Tools (all 13 tools)
5. Agent Core MVP (LLM loop, no context management / security)
6. CLI (async input + colored output)
7. Context Manager + Loop Detector
8. Security Layer
9. Error Recovery
10. Screenshot + Vision fallback
11. Linting + logger + polish + README (ruff + pyright clean)
12. Testing + demo recording

## Common pitfalls to avoid

- **Never hardcode selectors** like `a[data-qa='vacancy']` — always use `data-agent-ref` from parser
- **Never hardcode URLs or page-specific logic** — agent must figure it out via LLM
- **Always call wait_for_page_ready() after navigation** — SPA pages need DOM stability check
- **Always execute tool_calls sequentially** — browser state is inherently sequential
- **Always validate ref exists** before clicking — page may have changed since last get_page_state
- **Screenshots go as image_url content parts** in tool results, not as text
- **Use ensure_ascii=False** in json.dumps for Russian text
- **Run `uv run ruff check src/ --fix && uv run ruff format src/`** before committing
- **Never use pip or requirements.txt** — all deps managed through `uv add` / `pyproject.toml`