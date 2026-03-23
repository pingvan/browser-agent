# AI Browser Agent — Project Guide

## Project Overview

AI-агент, который управляет видимым браузером и решает многошаговые задачи через один orchestration loop:

`observe -> eval -> act -> observe`

Главный runtime — `src/agent/core.py`. Основной агент — единственный decision-maker. Отдельный `PageSummarizer` существует только для сжатия контекста страницы и не имеет права завершать задачу, выбирать действия или объявлять прогресс.

## Current Architecture

```
User CLI
  -> Agent (core.py)
     -> MessageManager          # multi-turn conversation accumulator
     -> OpenAI tool-calling     # native multi-turn with tool messages
     -> ToolRegistry            # tool schema + local validation
     -> BrowserManager          # browser execution layer
     -> Page parser / DOM       # DOM/text extraction
     -> PageSummarizer          # sidecar page compression (not integrated)
     -> LoopDetector            # local anti-loop hints
     -> SecurityLayer           # risky-action confirmation
```

### Message Flow (per step)

Each LLM call receives a full multi-turn conversation:
```
[system]    System prompt (static, includes guardrails)
[user]      Observation step 1 (text + screenshot)
[assistant] Tool calls step 1
[tool]      Results step 1
[user]      Observation step 2
...
[user]      Current observation
```

MessageManager accumulates messages via `add_observation()`, `add_assistant_tool_calls()`, `add_tool_result()`. Old steps are compressed when context grows large.

## Runtime Rules

- Нет planner-стадии и нет transition/state-evaluator-стадии.
- Нет автоматического завершения по lexical similarity страницы и задачи.
- Только `done` от основного агента завершает run.
- Разрешён максимум один browser action за шаг.
- `save_memory` хранит только устойчивые факты.
- `set_subtask` обновляет текущую minor task для следующих шагов.
- Loop detection локальная: repeated same action on same fingerprint, `AAA`, `ABAB`, repeated failures.

## Core Modules

1. `src/agent/core.py` — `Agent` runtime: run loop, _observe(), _decide(), _execute_tool_calls()
2. `src/agent/message_manager.py` — multi-turn conversation accumulator with compression
3. `src/agent/tool_registry.py` — tool schema + local validation
4. `src/agent/page_summarizer.py` — sidecar page compression (not integrated into core)
5. `src/agent/loop_detector.py` — local anti-loop hints (AAA, ABAB, page oscillation)
6. `src/agent/state.py` — AgentState TypedDict, state mutation helpers, fingerprinting
7. `src/agent/prompts.py` — system prompts for the main runtime
8. `src/browser/manager.py` — browser execution layer (Playwright)
9. `src/parser/page_parser.py` — DOM/text extraction
10. `src/security/security_layer.py` — risky-action confirmation и prompt-injection warnings

## Commands

```bash
uv run python -m src.main
uv run pytest -q
uv run ruff check .
uv run pyright
```

## Notes

- `architecture-v2.md` и прочие старые корневые design notes могут быть историческими и не должны считаться source of truth, если они расходятся с текущим runtime.
- Source of truth для текущей архитектуры: `src/agent/core.py` и соседние runtime-модули.
