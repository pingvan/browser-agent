# Agent Optimization — Tasks

Last Updated: 2026-03-22

---

## Скорость шагов: упрощение wait_for_page_ready

- [x] **1.1** Упростить `wait_for_page_ready()` в `src/browser/controller.py`: убрать Level 2 (DOM stability polling, 3s timeout) и Level 3 (spinner detection, 1.5s timeout), оставить только `domcontentloaded`
- [x] **1.2** Убрать auto extract+screenshot из `_PAGE_CHANGING_TOOLS` в `execute_tool()` (`src/browser/tools.py`). Page state/screenshot только при `get_page_state` и `navigate`
- [x] **1.3** Обновить промпт (`src/agent/prompts.py`): добавить указание вызывать `get_page_state` после действий, чтобы увидеть результат

---

## Acceptance Criteria

- [x] `wait_for_page_ready` содержит только `domcontentloaded` (без DOM stability и spinner detection)
- [x] click/type_text/select_option/press_key/scroll не возвращают page_state и screenshot автоматически
- [ ] Агент корректно вызывает `get_page_state` после действий
- [ ] Время шага уменьшилось (субъективно быстрее)
