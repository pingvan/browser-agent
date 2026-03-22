# Vision-First Strategy — Context

**Last Updated: 2026-03-22**

---

## Key Files

| Файл | Роль | Что менять |
|------|------|------------|
| `src/browser/tools.py` | Все browser tools | Убрать auto DOM из `_AUTO_STATE_TOOLS`, добавить auto-screenshot ко всем page-changing tools |
| `src/parser/page_parser.py` | DOM extraction + screenshot | Повысить screenshot quality, возможно разделить screenshot и DOM extraction |
| `src/agent/core.py` | Main loop | Адаптировать под screenshot-first observation |
| `src/agent/prompts.py` | System prompt | Фундаментальный пересмотр — vision-first execution protocol |
| `src/agent/context_manager.py` | Context window | MAX_SCREENSHOTS_KEPT 1→2-3, detail level управление |
| `src/agent/message_builder.py` | Message construction | Поддержка screenshot-only результатов, detail level параметр |
| `src/agent/action_dispatcher.py` | Action execution chain | Адаптировать format_chain_result под screenshot-only |
| `src/agent/tools_schema.py` | Tool definitions | Обновить описания tools (screenshot behavior) |

## Dependencies

- **OpenAI API vision**: GPT-4.1 поддерживает image_url content parts — уже используется
- **Playwright screenshot**: `page.screenshot(type="jpeg", quality=N)` — уже используется
- **No new packages required**

## External Constraints

- GPT-4.1 image token cost: ~85 tokens per image at `detail: "low"`, ~765 tokens at `detail: "auto"` (512×512 tiles)
- Screenshot JPEG at quality=65 ≈ 50-100KB → base64 ≈ 70-140KB
- OpenAI rate limits: vision requests count same as text

## Technical Decisions Log

### Decision 1: Screenshot quality = 65
- **Why**: quality=35 (current auto) слишком низкое для primary observation — текст нечитаем. quality=75 (current manual) избыточно. 65 — компромисс: текст читаем, файл ~60-80KB.
- **Alternative considered**: quality=50. Отклонено — на сложных страницах (таблицы, мелкий текст) 50 может быть недостаточно.

### Decision 2: Auto-screenshot для всех page-changing tools
- **Why**: Агент должен видеть результат КАЖДОГО действия. Иначе он слеп между get_page_state вызовами.
- **Scope**: navigate, click, go_back, switch_tab, scroll, type_text (press_enter=true), select_option, press_key, wait.
- **Exception**: hover, search_page — не меняют страницу визуально значимо.

### Decision 3: MAX_SCREENSHOTS_KEPT = 2
- **Why**: Агенту нужно видеть "до" и "после" действия для сравнения. 1 недостаточно. 3 — слишком много токенов.
- **Trade-off**: 2 screenshots × 85 tokens (low) = 170 tokens. Допустимо.

### Decision 4: get_page_state — единственный источник DOM
- **Why**: Инвертирует текущую модель. DOM extraction тяжёлая операция (~300ms + ~1-3K tokens). Делать её каждый шаг расточительно если агент просто наблюдает.
- **Risk**: Агент может забыть вызвать get_page_state перед click. Митигация: error message при invalid ref.

### Decision 5: navigate по-прежнему возвращает URL+title (без DOM)
- **Why**: URL и title — лёгкая метаинформация (из page.url и page.title), не требует JS injection. Агенту полезно знать куда он попал.
- **Implementation**: navigate returns {success, url, title, screenshot} — без page_state text и elements.

## Связь с существующими dev-docs

- **agent-optimization**: Phase 1 (task 1.2 — убрать auto extract) уже сделана для click/type/select/press_key/scroll. Эта задача идёт дальше — убирает auto extract и из navigate.
- **agent-perf-improvements**: Phase 2 (Performance) и Phase 4 (Vision) частично пересекаются. vision-first-strategy замещает их — после её реализации те задачи будут неактуальны или переформулированы.
