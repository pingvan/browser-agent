# Agent Performance Improvements — Tasks

**Last Updated: 2026-03-22**

---

## Phase 1: Observability & Logging [Effort: M]

> Цель: видеть в логах что происходит — время каждого инструмента, передачу скриншотов, размер DOM

- [ ] **1.1** Добавить timing decorator/helper для измерения времени выполнения async функций
  - Файл: `src/utils/logger.py`
  - Создать `async def timed(name, coro)` — логирует `[PERF] {name}: {elapsed_ms}ms`
  - AC: Каждый вызов инструмента в логах показывает время выполнения

- [ ] **1.2** Добавить timing логи в `execute_tool()` и `_do_action()`
  - Файл: `src/browser/tools.py`
  - Замерять: время _do_action, время extract_page_state_with_screenshot отдельно
  - AC: В логах видно `[PERF] click: action=120ms, page_state=450ms, screenshot=200ms`

- [ ] **1.3** Добавить timing логи в LLM API вызов в `core.py`
  - Файл: `src/agent/core.py`
  - Замерять: время `client.chat.completions.create()`, размер response
  - AC: В логах видно `[PERF] LLM call: 2340ms, response_tokens=850`

- [ ] **1.4** Логировать передачу скриншотов
  - Файл: `src/agent/action_dispatcher.py` и `src/agent/message_builder.py`
  - Когда screenshot_b64 передаётся в build_action_result → логировать `[SCREENSHOT] Attached: {size_kb}KB`
  - Когда screenshot strip-ается в context_manager → логировать `[SCREENSHOT] Stripped from message`
  - AC: В логах чётко видно, когда агенту передаётся скриншот и когда удаляется

- [ ] **1.5** Логировать качество DOM extraction
  - Файл: `src/parser/page_parser.py`
  - После extract: логировать `[DOM] Elements: {count}/{cap}, Text: {len}/{cap} chars, URL: {url}`
  - Если count == cap (достигнут лимит) → `[DOM] WARNING: element limit reached, some elements may be missing`
  - AC: В логах видно, достаточно ли элементов мы извлекаем

- [ ] **1.6** Добавить per-step summary лог
  - Файл: `src/agent/core.py`
  - В конце каждого шага: `[STEP {n}/{max}] Tool={name}, Success={bool}, Duration={total_ms}ms`
  - AC: Можно быстро просканировать лог и увидеть flow агента

---

## Phase 2: Performance — Wait Strategy & DOM Extraction [Effort: L]

> Цель: убрать лишнюю работу и сделать нормальное ожидание SPA-загрузки

- [ ] **2.1** Улучшить `wait_for_page_ready` для SPA
  - Файл: `src/browser/controller.py`
  - Стратегия: domcontentloaded → networkidle (с таймаутом 3с) → DOM stability check (mutation observer, 500ms без изменений)
  - Общий таймаут: 15с
  - AC: На hh.ru агент видит полностью загруженный DOM с вакансиями

- [ ] **2.2** Убрать двойное извлечение page_state в `execute_tool`
  - Файл: `src/browser/tools.py`
  - Сейчас: `_do_action("navigate")` делает `wait_for_page_ready()`, потом `execute_tool()` вызывает `extract_page_state_with_screenshot()` в секции `_AUTO_STATE_TOOLS`
  - Но `_do_action` НЕ извлекает page_state сам — он только возвращает `{success, url, title}`. Двойного извлечения DOM нет, но есть проблема: page_state извлекается ДО того, как SPA полностью загрузился (если `wait_for_page_ready` недостаточен)
  - **Реальный fix**: После улучшения wait_for_page_ready (2.1) — извлечение будет на полном DOM
  - AC: page_state содержит полный DOM после навигации

- [ ] **2.3** Добавить `wait_for_stable_dom` как отдельный инструмент
  - Файл: `src/browser/tools.py` + `src/agent/tools_schema.py`
  - Использует MutationObserver: ждёт пока DOM не перестанет меняться (500ms тишины, макс 5с)
  - AC: Агент может вызвать `wait_for_stable_dom()` когда страница грузится динамически

- [ ] **2.4** Оптимизировать `extract_page_state_with_screenshot` — параллельное выполнение
  - Файл: `src/parser/page_parser.py`
  - Сейчас: `asyncio.gather(extract_page_state, _take_screenshot)` — уже параллельно!
  - **Но**: `extract_page_state` внутри выполняет `page.evaluate(_JS_EXTRACT_ELEMENTS)` и `page.evaluate(_JS_EXTRACT_TEXT)` последовательно
  - Fix: объединить два JS evaluate в один вызов (один `page.evaluate` вместо двух)
  - AC: Одна JS injection вместо двух на каждый page_state

---

## Phase 3: DOM Quality — Parser Improvements [Effort: M]

> Цель: агент видит все интерактивные элементы, включая кастомные компоненты

- [ ] **3.1** Расширить CSS-селекторы для парсера
  - Файл: `src/parser/page_parser.py` → `_JS_EXTRACT_ELEMENTS`
  - Добавить: `[class*="btn"]`, `[class*="button"]`, `[class*="link"]`, `[data-testid]`, `[data-qa]`, `[aria-expanded]`, `[aria-haspopup]`, `label[for]`
  - AC: Кастомные кнопки hh.ru видны в interactive elements

- [ ] **3.2** Увеличить лимит элементов 150 → 250
  - Файл: `src/parser/page_parser.py` → `_JS_EXTRACT_ELEMENTS`
  - Изменить `if (results.length >= 150)` на 250
  - AC: Больше элементов на больших страницах

- [ ] **3.3** Увеличить лимит текста 4000 → 6000 символов
  - Файл: `src/parser/page_parser.py` → `_JS_EXTRACT_TEXT`
  - Изменить `4000` на `6000` в обоих местах
  - AC: Больше контекста для агента

- [ ] **3.4** Увеличить лимит текста элемента 80 → 120 символов
  - Файл: `src/parser/page_parser.py` → `_JS_EXTRACT_ELEMENTS`
  - Изменить `.slice(0, 80)` на `.slice(0, 120)`
  - AC: Длинные названия вакансий/кнопок не обрезаются

- [ ] **3.5** Добавить детекцию модальных окон/overlay
  - Файл: `src/parser/page_parser.py` → `_JS_EXTRACT_ELEMENTS` или новый JS
  - В начале extraction проверять: есть ли элемент с `position:fixed` или `z-index > 1000` покрывающий viewport
  - Если да — добавить `[MODAL DETECTED]` в page_state content
  - AC: Агент знает, что модальное окно блокирует страницу

- [ ] **3.6** Улучшить формат page_state — добавить структурные маркеры
  - Файл: `src/parser/page_parser.py` → `format_page_state`
  - Группировать элементы: навигация, формы, кнопки действий, ссылки
  - Добавить секцию `## Visible Forms` если есть input/select/textarea
  - AC: Агент лучше понимает структуру страницы

---

## Phase 4: Vision & Screenshot Improvements [Effort: M]

> Цель: скриншоты полезны — качество достаточное для чтения текста, агент знает что на них

- [ ] **4.1** Повысить quality скриншота в `_take_screenshot` с 35 до 50
  - Файл: `src/parser/page_parser.py`
  - Изменить `quality=35` на `quality=50`
  - AC: Текст на скриншотах читаем

- [ ] **4.2** Логировать размер скриншота после сжатия
  - Файл: `src/parser/page_parser.py`
  - После `base64.b64encode(data)` → `logger.debug(f"[SCREENSHOT] Captured: {len(data)//1024}KB")`
  - AC: В логах виден размер каждого скриншота

- [x] **4.3** Добавить в промпт guidance когда использовать screenshot
  - Файл: `src/agent/prompts.py`
  - Добавлена Section 7 "SPA & Dynamic Pages — Screenshot-First Loading Check" + Example 5
  - AC: Агент чаще использует скриншоты для верификации

- [ ] **4.4** Для explicit `screenshot()` tool использовать `detail: "auto"` вместо `"low"`
  - Файл: `src/agent/message_builder.py`
  - Добавить параметр `detail` в `build_action_result`
  - Когда tool = screenshot → `detail="auto"`, иначе `detail="low"`
  - AC: Прямой вызов screenshot() даёт более детальное изображение

---

## Phase 5: System Prompt Optimization [Effort: M]

> Цель: промпт более concise, actionable, с guidance по vision и SPA

- [ ] **5.1** Сократить примеры — убрать Example 1, 2, 5 (оставить 3 и 4 как наиболее важные)
  - Файл: `src/agent/prompts.py`
  - Примеры 1 (simple search) и 2 (pizza order) тривиальны
  - Пример 5 (Google pivot) понятен из Section 6
  - AC: Промпт короче на ~200 токенов

- [x] **5.2** Добавить секцию "SPA & Dynamic Pages"
  - Файл: `src/agent/prompts.py`
  - Добавлена Section 7 с mandatory protocol: screenshot → wait → get_page_state
  - AC: Агент знает как обращаться с SPA-сайтами

- [x] **5.3** Усилить guidance по screenshot verification
  - Файл: `src/agent/prompts.py`
  - Section 7: "NEVER blindly trust page_state text if screenshot contradicts it. Screenshot is ground truth."
  - AC: Агент приоритизирует visual verification

- [ ] **5.4** Добавить guidance по модальным окнам
  - Файл: `src/agent/prompts.py`
  - "If [MODAL DETECTED] appears in page_state — close the modal first (look for X/close button, press Escape) before interacting with elements behind it"
  - AC: Агент знает как обращаться с модалками

- [ ] **5.5** Добавить guidance по dropdown-ам
  - Файл: `src/agent/prompts.py`
  - "For custom dropdowns (not native `<select>`): click to open → wait → get_page_state to see options → click the option. Do NOT use select_option on non-`<select>` elements."
  - AC: Агент правильно работает с кастомными выпадашками

---

## Phase 6: Context & Loop Detection [Effort: S]

> Цель: агент не теряет важный контекст и быстрее выходит из зацикливания

- [ ] **6.1** Расширить окно loop detector с 4 до 8 действий
  - Файл: `src/agent/loop_detector.py`
  - Добавить детекцию ABCABC и более длинных паттернов
  - AC: Медленные петли обнаруживаются

- [ ] **6.2** Умные hints в loop detector — анализ типа зацикливания
  - Файл: `src/agent/loop_detector.py`
  - Если цикл на `click` → hint: "The click is not having the expected effect. Try screenshot() to see what's blocking"
  - Если цикл на `get_page_state` → hint: "You keep re-reading the page without acting. Choose an action"
  - AC: Подсказки специфичны, а не generic "try different approach"

- [ ] **6.3** Сбрасывать loop detector при смене URL
  - Файл: `src/agent/loop_detector.py` или `src/agent/core.py`
  - Если URL изменился → `loop_detector.reset()`
  - AC: Навигация на новую страницу не считается продолжением цикла

- [ ] **6.4** Не инжектить plan каждый шаг — только при изменении или каждые 5 шагов
  - Файл: `src/agent/context_manager.py`
  - Добавить логику: план в контекст только если plan.changed или step % 5 == 0
  - AC: Меньше bloat в контексте

---

## Summary

| Фаза | Усилие | Влияние | Приоритет |
|-------|--------|---------|-----------|
| Phase 1: Observability | M | Критически — без неё не видим эффект | 1 |
| Phase 2: Performance | L | Высокое — убирает главные bottleneck-и | 2 |
| Phase 3: DOM Quality | M | Высокое — агент видит больше элементов | 3 |
| Phase 4: Vision | M | Среднее — улучшает verification flow | 4 |
| Phase 5: Prompt | M | Среднее — лучший guidance агенту | 5 |
| Phase 6: Context | S | Низкое — fine-tuning | 6 |
