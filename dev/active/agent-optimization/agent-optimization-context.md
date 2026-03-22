# Agent Optimization — Context

Last Updated: 2026-03-22

---

## Key Files

| File | Role | What to change |
|------|------|----------------|
| `src/browser/controller.py` | `wait_for_page_ready()` | Убрать Level 2 (DOM stability) и Level 3 (spinner detection) |
| `src/browser/tools.py` | `execute_tool()` + `_PAGE_CHANGING_TOOLS` | Убрать auto page_state/screenshot для всех кроме navigate |
| `src/agent/prompts.py` | System prompt | Добавить указание вызывать `get_page_state` после действий |

---

## Technical Decisions Log

### Decision 1: wait_for_page_ready → только domcontentloaded
- **Убираем:** Level 2 (DOM stability polling, 3s), Level 3 (spinner detection, 1.5s)
- **Оставляем:** Level 1 (domcontentloaded)
- **Rationale:** У агента есть `wait` tool (tools.py:275). Если страница не дозагрузилась — агент увидит это в page_state и вызовет `wait` сам.

### Decision 2: Lazy page state — только при navigate
- **Было:** auto extract+screenshot для всех `_PAGE_CHANGING_TOOLS` (7 tools)
- **Стало:** auto page state только для `navigate`, остальные — агент вызывает `get_page_state` явно
- **Rationale:** Убирает 150-400ms overhead на каждое действие. Агент контролирует когда ему нужно "видеть" страницу.

### Decision 3: Оптимизация токенов/промпта — отложена
- Стратегия: "make it work, then optimize". Сначала поднять tier в OpenAI для снятия TPM лимита, потом оптимизировать если нужно.

---

## Constraints

- Не менять CI конфигурацию
- Код должен пройти ruff + pyright
- Python 3.12+, async/await
