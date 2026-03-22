# Agent Performance Improvements — Context

**Last Updated: 2026-03-22**

## Key Files

| File | Role | What to change |
|------|------|----------------|
| `src/utils/logger.py` | Logging | Добавить timing, structured logging, screenshot tracking |
| `src/browser/controller.py` | Browser control | Улучшить `wait_for_page_ready` для SPA |
| `src/browser/tools.py` | Tool execution | Убрать двойную извлечение DOM, добавить timing логи |
| `src/parser/page_parser.py` | DOM extraction | Увеличить лимиты, добавить кастомные селекторы, улучшить quality скриншотов |
| `src/agent/core.py` | Main loop | Добавить timing на LLM вызовы, логировать токены |
| `src/agent/prompts.py` | System prompt | Сократить примеры, добавить vision guidance |
| `src/agent/context_manager.py` | Context window | Оптимизировать screenshot management |
| `src/agent/action_dispatcher.py` | Action chain | Добавить timing логи на каждый action |
| `src/agent/message_builder.py` | Message construction | Логировать размер сообщений |
| `src/agent/loop_detector.py` | Loop detection | Расширить окно анализа, добавить smart hints |

## Technical Decisions Log

### Decision 1: Модель gpt-4o vs gpt-4.1
- **Факт**: В `core.py:57` используется `model="gpt-4o"`, хотя в CLAUDE.md указан `gpt-4.1`
- **Действие**: Оставляем gpt-4o пока, это не влияет на performance improvements

### Decision 2: JSON mode vs tool calling
- **Факт**: Используется `response_format={"type": "json_object"}` вместо function calling
- **Действие**: Не меняем — это архитектурное решение, не связанное с perf

### Decision 3: Screenshot quality 35 vs 75
- **Факт**: `_take_screenshot` в page_parser.py использует quality=35, а `screenshot` tool — quality=75
- **Решение**: Поднять до 50 в _take_screenshot (компромисс размер/качество)

### Decision 4: Image detail level
- **Факт**: В message_builder.py screenshots передаются с `detail: "low"`
- **Решение**: Оставляем "low" — это уменьшает token count. Для explicit `screenshot()` tool можно рассмотреть "auto"

## Dependencies

- Все фазы можно делать параллельно, кроме:
  - Phase 1 (Observability) желательно первой — чтобы измерять эффект остальных фаз
  - Phase 5 (Prompt) зависит от Phase 3 (DOM) — сначала улучшаем DOM, потом обновляем guidance
- Внешние зависимости: нет новых пакетов

## Current Architecture Notes

### Поток выполнения инструмента
```
core.py: run_agent()
  → parse JSON response
  → pre-scan for meta-tools (create_plan, update_plan, ask_human, done)
  → security check (is_dangerous)
  → action_dispatcher.dispatch_actions()
    → execute_browser_tool() / execute_file_tool()
      → _do_action() — выполняет действие
      → extract_page_state_with_screenshot() — ДЛЯ AUTO_STATE_TOOLS (навигация)
    → format_chain_result()
  → build_action_result() с screenshot
  → context_manager.prepare()
  → LLM API call
```

### Где теряется время
1. `_do_action("navigate")` → `page.goto()` + `wait_for_page_ready()` = ~1-3с
2. `extract_page_state_with_screenshot()` = page.evaluate() * 2 + screenshot = ~0.5-1с
3. LLM API call = ~1-3с
4. **Итого per step: 3-7с** (без учёта реального ожидания страницы)
