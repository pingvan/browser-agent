# Vision-First Strategy — Plan

**Last Updated: 2026-03-22**

---

## Executive Summary

Переход агента от DOM-first к Vision-first стратегии наблюдения. Сейчас агент после каждого действия получает DOM (текстовое представление страницы + список интерактивных элементов) + скриншот, и думает преимущественно по тексту DOM. Новая стратегия: **агент "думает глазами"** — после каждого действия получает только скриншот, анализирует его визуально, и запрашивает DOM только когда готов взаимодействовать (видит, что страница загружена и нужно кликнуть/ввести текст).

## Почему это важно

1. **Скорость**: DOM extraction (~200-500ms) + screenshot (~100ms) на каждом шаге. Если DOM извлекается только когда нужен — экономим ~300ms на шагах наблюдения
2. **Точность**: LLM с vision отлично понимает страницы визуально. Скриншот — ground truth, DOM — производная. Сейчас агент может "не видеть" элемент на странице, если JS-парсер его пропустил
3. **SPA-корректность**: На SPA-страницах (React, Vue) DOM может быть пустым/неполным пока идёт рендер. Скриншот показывает реальное состояние — спиннер, skeleton, или готовый контент
4. **Естественность**: Человек сначала смотрит на страницу, потом решает что кликнуть. Агент должен работать так же
5. **Токены**: Текстовое представление DOM (page_state) — ~1-3K токенов. Если оно не нужно на каждом шаге, экономим контекст

## Current State (DOM-First)

```
Action → wait_for_page_ready → [auto: DOM extraction + screenshot] → LLM gets both → thinks via DOM text
                                                                                      (screenshot = supplementary)
```

- `_AUTO_STATE_TOOLS` = {navigate, click, go_back, switch_tab} — автоматически возвращают page_state + screenshot
- `get_page_state` — ручной вызов, тоже DOM + screenshot
- Промпт ориентирован на DOM: "Interactive Elements [0] link... [1] button..."
- Screenshot quality=35 (очень низкое) для page_state, quality=75 для ручного screenshot()
- Агент решает что делать по текстовому списку элементов

## Proposed Future State (Vision-First)

```
Action → wait → [auto: screenshot only] → LLM sees the page visually
                                          ↓
                              "Page loaded? I see the search form."
                              "I need to click the search button."
                                          ↓
                              [explicit: get_page_state] → DOM with refs
                                          ↓
                              "Ref [5] is the search button → click(5)"
```

### Новый цикл наблюдения

1. **Observe (screenshot)** — после каждого действия агент получает скриншот
2. **Think (vision)** — агент анализирует что видит: загрузилась ли страница? что на ней? нужно ли ждать?
3. **Decide** — два пути:
   - **Нужно взаимодействие** → вызывает `get_page_state()` чтобы получить ref-ы → кликает/вводит
   - **Нужно подождать** → вызывает `wait()` или `screenshot()` → снова Observe
   - **Нужно скроллить/навигировать** → scroll/navigate не требует ref-ов, можно сразу
4. **Act** — выполняет действие → получает новый скриншот → цикл

### Ключевые изменения

| Аспект | Было (DOM-first) | Стало (Vision-first) |
|--------|------------------|----------------------|
| Primary observation | DOM text + elements | Screenshot |
| Screenshot quality | 35 (auto) / 75 (manual) | 65+ (всегда) |
| DOM extraction | Автоматически после navigate/click | Только по запросу (get_page_state) |
| Auto-state tools | navigate, click, go_back, switch_tab | Только screenshot после действий |
| Thinking basis | Текстовый список "[0] button..." | Визуальный анализ скриншота |
| get_page_state роль | Обновить DOM (уже есть) | Запросить DOM для взаимодействия |
| Количество DOM на задачу | Каждый шаг (~50) | Только когда нужен (~15-20) |
| Screenshot в контексте | MAX=1, остальные strip | MAX=2-3, основной источник инфо |

---

## Implementation Phases

### Phase 1: Screenshot Infrastructure [Effort: M]
> Цель: скриншоты высокого качества, auto-screenshot после всех действий, без auto-DOM

Изменения:
- Убрать auto DOM extraction из всех инструментов (navigate, click, go_back, switch_tab)
- Все page-changing tools возвращают только screenshot (+ URL/title для контекста)
- Повысить quality скриншота до 65
- `get_page_state()` — единственный способ получить DOM

### Phase 2: Prompt Rewrite [Effort: XL]
> Цель: агент понимает новую стратегию — смотрит → думает → запрашивает DOM → действует

Фундаментальный пересмотр промпта:
- Execution protocol: screenshot-first loop
- Когда запрашивать DOM: "I see the form is loaded, I need to fill it → get_page_state"
- Когда НЕ запрашивать DOM: "Page is loading (spinner visible)", "I need to scroll down to see more"
- Примеры адаптированы под vision-first

### Phase 3: Tool Behavior [Effort: M]
> Цель: инструменты возвращают screenshot по умолчанию, DOM — отдельно

- Auto-screenshot для ВСЕХ browser tools (не только page-changing)
- `get_page_state` остаётся текущим (DOM + screenshot)
- Добавить lightweight feedback без screenshot для scroll/wait (опционально)
- Убрать screenshot tool как отдельный (screenshot идёт автоматически)

### Phase 4: Context Management [Effort: M]
> Цель: контекст адаптирован под больше скриншотов, меньше DOM текста

- Увеличить MAX_SCREENSHOTS_KEPT с 1 до 2-3
- Адаптировать sliding window — screenshot-heavy messages меньше по тексту
- Оптимизировать detail level: последний screenshot="auto", предыдущие="low"

### Phase 5: Testing & Tuning [Effort: L]
> Цель: протестировать на реальных задачах, подтюнить

- Тест на SPA (hh.ru, LinkedIn)
- Тест на формах (Google, login pages)
- Тест на multi-step задачах
- Подобрать оптимальный screenshot quality (balance: token cost vs readability)
- Подобрать оптимальный MAX_SCREENSHOTS_KEPT

---

## Risk Assessment

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| LLM хуже понимает скриншоты чем DOM text | Низкая | Высокое | GPT-4.1 отлично работает с vision. Всегда можно вызвать get_page_state |
| Агент не запрашивает DOM когда нужно | Средняя | Высокое | Чёткие примеры в промпте + fallback: если click без ref → hint "call get_page_state first" |
| Больше шагов на задачу (screenshot → think → DOM → act вместо DOM+screenshot → act) | Средняя | Среднее | Шаги быстрее (нет DOM extraction), общее время может быть сопоставимым |
| Увеличение token cost из-за скриншотов | Средняя | Среднее | quality=65, detail="low" для старых, strip после 2-3 |
| Регрессия на задачах где DOM text был critical | Низкая | Среднее | get_page_state всегда доступен, агент может вызвать |

## Success Metrics

1. **Скорость шага**: снижение среднего времени шага на 20-30% (за счёт отсутствия DOM extraction)
2. **SPA корректность**: агент корректно ждёт загрузки SPA страниц (не кликает по skeleton)
3. **Количество DOM запросов**: снижение с ~1 на шаг до ~0.3-0.5 на шаг
4. **Успешность задач**: не хуже текущей (regression test)
5. **Token usage**: сопоставимый или ниже (меньше DOM text, больше screenshot, но screenshot = ~85 tokens на low detail)

---

## Архитектурные решения

### Q1: Какие tools возвращают auto-screenshot?
**Решение**: Все browser tools, меняющие визуальное состояние: navigate, click, go_back, switch_tab, scroll, type_text (с press_enter), select_option, press_key.
**Без screenshot**: wait (пауза — ничего не изменилось), hover (минимальное изменение), search_page (текстовый поиск).
**Но**: wait может быть полезен с screenshot (чтобы увидеть, загрузилось ли). Решение: wait возвращает screenshot.

### Q2: Что происходит с navigate?
**Решение**: navigate сейчас авто-возвращает DOM+screenshot. В новой стратегии navigate → wait_for_page_ready → screenshot only. Агент видит результат навигации, решает: "страница загружена? → get_page_state" или "spinner → wait → screenshot".

### Q3: Что с click — он сейчас требует ref?
**Решение**: click(ref) по-прежнему требует ref. Значит перед click агент ДОЛЖЕН вызвать get_page_state, чтобы получить ref-ы. Это естественный flow: see → get_refs → click.

### Q4: Можно ли scroll/navigate без DOM?
**Да**: scroll(direction, amount) не нужны ref-ы. navigate(url) не нужны ref-ы. Агент может скроллить и навигировать чисто визуально.

### Q5: Как агент будет знать что он видит на странице без DOM text?
**Решение**: Через vision! GPT-4.1 видит скриншот и может описать: "I see a search page with a text input and a 'Search' button. There are 10 search results below." Промпт должен научить агента так думать.

### Q6: Как обрабатывать случай когда агент вызвал click без предварительного get_page_state?
**Решение**: Не блокировать — возможно у агента есть ref из предыдущего get_page_state. Но если ref невалиден (элемент не найден) — в error message подсказать: "Element ref not found. Did the page change? Call get_page_state() to refresh interactive elements."
