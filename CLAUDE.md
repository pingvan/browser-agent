# AI Browser Agent — Project Guide

## Project Overview

AI-агент, который управляет видимым браузером и решает многошаговые задачи через один orchestration loop:

`observe -> eval -> act -> observe`

Главный runtime — `src/agent/core.py`. Основной агент — единственный decision-maker. Отдельный `PageSummarizer` существует только для сжатия контекста страницы и не имеет права завершать задачу, выбирать действия или объявлять прогресс.

## Current Architecture

```
User CLI
  -> Agent
     -> MessageManager
     -> OpenAI tool-calling decision
     -> ToolRegistry / local validation
     -> BrowserManager
     -> Page parser / DOM extraction
     -> PageSummarizer (sidecar, cache-backed)
     -> LoopDetector / SecurityLayer
```

## Runtime Rules

- Нет planner-стадии и нет transition/state-evaluator-стадии.
- Нет автоматического завершения по lexical similarity страницы и задачи.
- Только `done` от основного агента завершает run.
- Разрешён максимум один browser action за шаг.
- `save_memory` хранит только устойчивые факты.
- `set_subtask` обновляет текущую minor task для следующих шагов.
- Loop detection локальная: repeated same action on same fingerprint, `AAA`, `ABAB`, repeated failures.

## Core Modules

1. `src/agent/core.py` — `Agent` runtime и `run_agent()`
2. `src/agent/message_manager.py` — сборка prompt context
3. `src/agent/tool_registry.py` — tool schema + local validation
4. `src/agent/page_summarizer.py` — sidecar page compression
5. `src/agent/loop_detector.py` — local anti-loop hints
6. `src/browser/manager.py` — browser execution layer
7. `src/parser/page_parser.py` — DOM/text extraction
8. `src/security/security_layer.py` — risky-action confirmation и prompt-injection warnings

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
