# Vision-First Strategy — Tasks

**Last Updated: 2026-03-22**

---

## Phase 1: Screenshot Infrastructure [Effort: M]

> Цель: скриншоты — primary observation. Auto-screenshot после действий, без auto-DOM.

- [ ] **1.1** Убрать auto DOM extraction из `_AUTO_STATE_TOOLS` в `src/browser/tools.py`
  - navigate, click, go_back, switch_tab больше не вызывают `extract_page_state_with_screenshot()`
  - navigate возвращает `{success, url, title}` + screenshot (без page_state text/elements)
  - click/go_back/switch_tab возвращают `{success, ...}` + screenshot
  - AC: Ни один tool не вызывает `extract_page_state()` автоматически

- [ ] **1.2** Добавить auto-screenshot ко всем page-changing tools
  - Tools с auto-screenshot: navigate, click, go_back, switch_tab, scroll, type_text (при press_enter=True), select_option, press_key, wait
  - Вынести `_take_screenshot()` в отдельную helper-функцию в tools.py (или переиспользовать из page_parser)
  - Возвращать screenshot_b64 в result dict
  - AC: После каждого page-changing действия LLM получает скриншот

- [ ] **1.3** Повысить screenshot quality с 35 до 65
  - Файл: `src/parser/page_parser.py` → `_take_screenshot()`
  - AC: Текст на скриншотах читаем для LLM

- [ ] **1.4** `get_page_state()` остаётся единственным источником DOM
  - Проверить, что get_page_state по-прежнему возвращает DOM + screenshot
  - Обновить docstring/описание: "Call this when you need interactive element refs for clicking/typing"
  - AC: get_page_state работает как раньше (DOM text + elements + screenshot)

- [ ] **1.5** Обновить `action_dispatcher.py` → `format_chain_result()`
  - Адаптировать под новый формат результатов (screenshot без page_state для большинства tools)
  - `get_chain_screenshot()` должен работать с новым форматом
  - AC: Chain result корректно форматируется с screenshot-only результатами

---

## Phase 2: Prompt Rewrite — Vision-First Protocol [Effort: XL]

> Цель: агент понимает что скриншот — его глаза, DOM — справочник для ref-ов.

- [ ] **2.1** Переписать Execution Protocol (Section 5)
  - Новый 4-step loop: **Observe** (screenshot) → **Think** (что вижу?) → **Plan** (что делать?) → **Act**
  - Чётко указать: "Your primary input is the screenshot. You SEE the page."
  - "Call get_page_state() ONLY when you need element refs to click/type/select"
  - AC: Промпт содержит vision-first execution loop

- [ ] **2.2** Переписать описание инструментов (Section 8)
  - screenshot: убрать как отдельный tool (идёт автоматически) ИЛИ оставить для "высокодетальный скриншот"
  - get_page_state: "Use this to get interactive element refs [0], [1]... when you need to click or type. Returns DOM text + elements list."
  - navigate/click/scroll/etc: "Returns screenshot automatically"
  - AC: Описания инструментов соответствуют новому поведению

- [ ] **2.3** Переписать SPA & Dynamic Pages (Section 7)
  - Новый протокол: action → screenshot → "вижу спиннер" → wait(2) → screenshot → "вижу контент" → get_page_state → interact
  - Убрать "mandatory get_page_state after navigate" — теперь это по решению агента
  - AC: SPA protocol основан на визуальном наблюдении

- [ ] **2.4** Переписать Recovery Protocol (Section 6)
  - "On failure: LOOK at the screenshot. What do you see? Is the element visible? Is there a modal blocking?"
  - Убрать "mandatory screenshot on failure" — screenshot уже идёт автоматически
  - AC: Recovery основан на визуальном анализе автоматического скриншота

- [ ] **2.5** Обновить примеры (Section 13)
  - Переписать 2-3 ключевых примера под vision-first flow
  - Пример: navigate → [screenshot shows search page] → "I see a search field and a button" → get_page_state → click(ref)
  - Пример: click → [screenshot shows spinner] → wait(2) → [screenshot shows results] → get_page_state → extract data
  - AC: Примеры демонстрируют vision-first мышление

- [ ] **2.6** Обновить Verification Checklist (Section 12)
  - "Ground all data in what you SAW in screenshots during this session"
  - AC: Верификация основана на визуальном опыте

- [ ] **2.7** Добавить anti-patterns
  - "NEVER call get_page_state on every step — only when you need refs"
  - "NEVER ignore the screenshot — it is your eyes"
  - "NEVER click without first having refs from get_page_state (unless using navigate/scroll which don't need refs)"
  - AC: Агент знает что НЕ делать

---

## Phase 3: Tool Behavior Adaptation [Effort: M]

> Цель: tools корректно работают в vision-first режиме

- [ ] **3.1** Обновить `tools_schema.py` — описания tools для LLM
  - navigate: "Go to URL. Returns screenshot of the loaded page."
  - click: "Click element by ref number. Call get_page_state() first to get refs. Returns screenshot."
  - get_page_state: "Get interactive elements with ref numbers for clicking/typing. Use when you need to interact."
  - scroll: "Scroll the page. Returns screenshot of new viewport."
  - wait: "Wait for page to load. Returns screenshot after waiting."
  - AC: Все tool descriptions отражают vision-first поведение

- [ ] **3.2** Обработка click с невалидным ref
  - В `_do_action("click")`: если ref не найден → error message включает hint
  - Hint: "Element [N] not found. The page may have changed. Call get_page_state() to refresh element refs."
  - AC: Агент получает actionable feedback при stale refs

- [ ] **3.3** Добавить URL+title к screenshot-only результатам
  - Для navigate, go_back, switch_tab: возвращать `{success, url, title, screenshot_b64}`
  - Для click, scroll, press_key: возвращать `{success, screenshot_b64}` (URL может не измениться)
  - AC: Агент знает URL после навигации без DOM extraction

- [ ] **3.4** Решить судьбу `screenshot()` tool
  - Вариант A: Убрать (авто-screenshot делает его избыточным)
  - Вариант B: Оставить как "high-detail screenshot" (quality=85, detail="auto") для сложных страниц
  - **Рекомендация**: Вариант B — оставить для случаев когда агенту нужен более детальный вид
  - AC: screenshot() tool обоснованно присутствует или убран

---

## Phase 4: Context Management [Effort: M]

> Цель: контекст оптимизирован под vision-first (больше скриншотов, меньше DOM text)

- [ ] **4.1** Увеличить MAX_SCREENSHOTS_KEPT с 1 до 2
  - Файл: `src/agent/context_manager.py`
  - AC: Агент видит последние 2 скриншота (до и после действия)

- [ ] **4.2** Дифференцировать detail level скриншотов
  - Последний скриншот: `detail: "auto"` (полное разрешение)
  - Предыдущие скриншоты: `detail: "low"` (экономия токенов)
  - Файл: `src/agent/context_manager.py` и `src/agent/message_builder.py`
  - AC: Свежий скриншот в высоком качестве, старые — в низком

- [ ] **4.3** Адаптировать текстовое представление action results
  - Для screenshot-only результатов: минимальный текст ("Clicked element [5]. Screenshot below." + screenshot)
  - Для get_page_state: полный DOM text как сейчас
  - Файл: `src/agent/action_dispatcher.py` → `format_chain_result()`
  - AC: Текстовый overhead минимален для screenshot-only шагов

- [ ] **4.4** Подумать о task reminder с визуальным контекстом
  - Task reminder каждые 10 шагов: включать ли последний скриншот?
  - **Решение**: нет — скриншот уже есть в последнем action result. Reminder текстовый.
  - AC: Task reminder не дублирует скриншоты

---

## Phase 5: Integration & Testing [Effort: L]

> Цель: всё работает вместе, нет регрессий

- [ ] **5.1** End-to-end тест: простой поиск (Google)
  - navigate → [screenshot] → "вижу поле поиска" → get_page_state → type_text → [screenshot] → "вижу результаты"
  - AC: Агент успешно выполняет поиск в vision-first режиме

- [ ] **5.2** End-to-end тест: SPA (hh.ru — поиск вакансий)
  - navigate → [screenshot: loading] → wait → [screenshot: loaded] → get_page_state → interact
  - AC: Агент корректно ждёт загрузки SPA

- [ ] **5.3** End-to-end тест: multi-step (форма с несколькими полями)
  - get_page_state (один раз) → type_text field1 → type_text field2 → click submit
  - AC: Агент не вызывает get_page_state перед каждым type_text

- [ ] **5.4** Проверить token usage
  - Сравнить среднее количество токенов на задачу: before vs after
  - AC: Token usage ≤ 110% от baseline (допустимый overhead от скриншотов)

- [ ] **5.5** Проверить время выполнения
  - Сравнить среднее время шага и общее время задачи
  - AC: Среднее время шага снизилось (меньше DOM extractions)

- [ ] **5.6** Regression: агент не зацикливается на get_page_state
  - Проверить что loop detector корректно ловит паттерн "get_page_state → get_page_state → get_page_state"
  - AC: Loop detector работает с новым паттерном

---

## Summary

| Фаза | Усилие | Влияние | Приоритет |
|-------|--------|---------|-----------|
| Phase 1: Screenshot Infrastructure | M | Критически — основа для всего | 1 |
| Phase 2: Prompt Rewrite | XL | Критически — без этого агент не поймёт новую стратегию | 2 |
| Phase 3: Tool Behavior | M | Высокое — корректная работа инструментов | 3 |
| Phase 4: Context Management | M | Среднее — оптимизация | 4 |
| Phase 5: Integration & Testing | L | Критически — подтверждение что всё работает | 5 |
