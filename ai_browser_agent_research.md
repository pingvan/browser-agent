# Исследование: AI Browser Agent — технологический стек и архитектура

---

## 1. Сравнение библиотек автоматизации браузера

### Сводная таблица

| Критерий | Playwright | Puppeteer | Selenium |
|---|---|---|---|
| **Persistent sessions** | `launchPersistentContext(userDataDir)` — полная поддержка. Сохраняет куки, localStorage, авторизацию между запусками | `launch({userDataDir})` — аналогично, через передачу пути к профилю Chrome | Через `ChromeOptions.add_argument('--user-data-dir=...')`. Работает, но менее документировано |
| **Headed режим** | Стабильный. `headless: false` — дефолт для persistent context. Нативная поддержка Chromium, Firefox, WebKit | Стабильный для Chromium. `headless: false`. Только Chromium/Chrome | Стабильный. Поддержка всех браузеров через WebDriver. Наиболее зрелый headed-режим |
| **Извлечение DOM** | `page.accessibility.snapshot()` — accessibility tree. `page.content()` — полный HTML. `page.evaluate()` — произвольный JS. `locator.ariaSnapshot()` — YAML accessibility snapshot | `page.accessibility.snapshot()` — аналогично (CDP). `page.content()`, `page.evaluate()` | `driver.page_source` — HTML. `execute_script()` — JS. Нет нативного accessibility tree |
| **Перехват сети** | `page.route()` — мощный перехват. `page.on('request')`, `page.on('response')` | `page.setRequestInterception(true)` — аналогично | Через Selenium Wire (сторонний пакет) или BrowserMob Proxy. Нет встроенного |
| **Попапы, iframes** | Встроенная поддержка: `page.frame()`, `page.on('dialog')`, `page.on('popup')` | `page.frames()`, `page.on('dialog')`. Хуже с cross-origin iframes | `driver.switch_to.frame()`, `driver.switch_to.alert()`. Работает, но более verbose |
| **Языки** | TypeScript/JavaScript (основной), Python, Java, C# | JavaScript/TypeScript только | Java, Python, C#, JavaScript, Ruby, Kotlin — максимальный охват |
| **Скриншоты** | `page.screenshot()` — full page, clip region, element-level. JPEG/PNG | `page.screenshot()` — аналогично | `driver.get_screenshot_as_png()` — базовый, без full-page scroll |
| **Комьюнити** | ~70K stars GitHub. Активная разработка Microsoft. Отличная документация | ~89K stars. Поддержка Google Chrome team. Хорошая документация | Старейший проект. Огромное комьюнити. Документация обширная, но местами устаревшая |
| **CDP доступ** | `page.context().newCDPSession()` — прямой доступ к Chrome DevTools Protocol | Нативный CDP — это основа Puppeteer | Через `ChromiumDriver.executeCdpCommand()`. Ограниченный |

### Рекомендация

**Playwright** — лучший выбор для данного проекта по совокупности факторов:

1. **Persistent context** — `launchPersistentContext()` идеально подходит для сохранения авторизованных сессий
2. **Accessibility tree** — `page.accessibility.snapshot()` + `locator.ariaSnapshot()` дают структурированное представление страницы для LLM
3. **Мультиязычность** — одинаково хорошая поддержка Python и TypeScript
4. **CDP доступ** — для продвинутых сценариев можно обращаться напрямую к протоколу DevTools
5. **Стабильность headed-режима** — отлично работает с видимым браузером

Стоит отметить, что browser-use (81K+ stars) и Stagehand двигаются к прямому CDP вместо Playwright для AI-автоматизации, так как CDP даёт более тонкий контроль над accessibility tree и iframes. Однако для тестового задания Playwright остаётся оптимальным балансом удобства и функциональности.

---

## 2. Сравнение AI-провайдеров

### Сводная таблица

| Критерий | Anthropic Claude | OpenAI GPT |
|---|---|---|
| **Рекомендуемая модель** | Claude Sonnet 4.5 (баланс цена/качество) или Sonnet 4.6 | GPT-4.1 (для tool use) или GPT-5.4 mini (новейшая) |
| **Контекстное окно** | До 1M токенов (Opus 4.6, Sonnet 4.6). Стандарт — 200K | GPT-4.1: 1M токенов. GPT-5.4: 1.05M токенов |
| **Max output** | 128K токенов (Opus 4.6) | 128K токенов (GPT-5.4) |
| **Tool use формат** | Поле `tools` в запросе. Описание через JSON Schema: `{name, description, input_schema}`. Ответ — content blocks с `type: "tool_use"` | Поле `tools` с `type: "function"`. JSON Schema в `function.parameters`. Ответ — `tool_calls` массив. Новый Responses API: `type: "custom"` |
| **Parallel tool calls** | Поддерживается — модель возвращает несколько tool_use блоков | `parallel_tool_calls: true` (по умолчанию включено) |
| **Vision** | Полная поддержка. Изображения как `type: "image"` в content с base64 или URL | Полная поддержка. Изображения в content messages |
| **Streaming** | SSE streaming с `stream: true`. Events: `content_block_delta`, `message_delta` | SSE streaming. Events: `response.output_item.added`, `response.content_part.delta` |
| **Цена (input/output за 1M)** | Sonnet 4.5: $3 / $15. Opus 4.6: $5 / $25. Haiku 4.5: $1 / $5 | GPT-4.1: $2 / $8. GPT-5.4 mini: $0.75 / $4.50. GPT-5.4: $2.50 / $10 |
| **Prompt caching** | Встроенный — до 90% экономии на повторяемом контексте. `cache_control` поле | Автоматический — cached input по сниженной цене (50–90% скидка) |
| **SDK** | Python: `anthropic`. TypeScript: `@anthropic-ai/sdk`. Оба официальные, типизированные | Python: `openai`. TypeScript: `openai`. Зрелые, хорошо поддерживаемые |
| **Качество tool use** | Превосходное следование инструкциям. Модели Claude исторически сильны в structured output | GPT-4.1 специально оптимизирован для tool calling (лидер бенчмарков). GPT-5.4 — built-in computer use |
| **Computer use** | Claude Computer Use — встроенная функция для управления рабочим столом через скриншоты | GPT-5.4 — первая модель OpenAI с built-in computer use |

### Рекомендация

**Claude Sonnet 4.5** — оптимальный выбор для агента:

1. **$3/$15 за 1M токенов** — разумная цена при хорошем качестве
2. **Отличный tool use** — Claude традиционно силён в следовании сложным инструкциям с tools
3. **Vision** — можно отправлять скриншоты страниц как fallback
4. **200K контекста** — достаточно для большинства агентных сценариев
5. **Prompt caching** — значительная экономия при повторяющемся системном промпте с описанием tools

Альтернатива: **GPT-4.1** ($2/$8) — дешевле и специально оптимизирован для function calling с 1M контекстом. Если бюджет критичен, это сильный вариант.

---

## 3. Подходы к извлечению информации со страницы

### 3.1 Accessibility Tree

**Суть:** Браузер строит дерево доступности (используемое screen readers) — упрощённое представление DOM с ролями, именами и состояниями элементов.

**API в Playwright:**
```python
# Python
snapshot = await page.accessibility.snapshot()
# Возвращает dict с деревом узлов

# Или через aria snapshot (YAML формат)
yaml_snapshot = await page.locator("body").aria_snapshot()
```

**Пример вывода (YAML формат из Playwright MCP):**
```yaml
- banner:
  - heading "Playwright enables reliable testing" [level=1]
  - link "Get started"
- main:
  - heading "Any browser · Any platform · One API"
  - textbox "Search docs" [ref=e15]
  - button "Submit" [ref=e21]
- navigation:
  - link "Docs"
  - link "API"
```

**Плюсы:**
- Компактное представление (типичная страница: 2–10K токенов)
- Содержит семантическую информацию: роли, имена, состояния (disabled, expanded и т.д.)
- Элементы имеют ref-идентификаторы для прямого взаимодействия через `getByRole`
- Playwright MCP использует именно этот подход как основной

**Минусы:**
- Не все элементы попадают в дерево (декоративные, custom components без ARIA)
- Shadow DOM может быть невидим (критично для Gmail и подобных)
- На сложных страницах (Reddit, SPA) может быть 50K+ токенов
- Теряется визуальное расположение элементов

### 3.2 Упрощённый DOM (Interactive Elements)

**Суть:** Парсинг HTML с извлечением только интерактивных элементов (кнопки, ссылки, инпуты, select) и присвоением уникальных ID для обращения.

**Реализация (подход browser-use):**
```python
# Внедряем JS на страницу для извлечения интерактивных элементов
elements = await page.evaluate("""
() => {
  const interactive = document.querySelectorAll(
    'a, button, input, select, textarea, [role="button"], [onclick], [tabindex]'
  );
  return Array.from(interactive).map((el, i) => ({
    id: i,
    tag: el.tagName,
    role: el.getAttribute('role') || el.tagName.toLowerCase(),
    text: el.innerText?.slice(0, 100) || '',
    placeholder: el.placeholder || '',
    href: el.href || '',
    type: el.type || '',
    visible: el.offsetParent !== null
  })).filter(e => e.visible);
}
""")
```

**Пример вывода:**
```
[0] link "Главная" href="/"
[1] link "Каталог" href="/catalog"
[2] input[text] placeholder="Поиск товаров"
[3] button "Найти"
[4] link "Войти" href="/login"
[5] button "Корзина (3)"
```

**Плюсы:**
- Очень компактно (обычно 1–5K токенов)
- Легко привязать ID к действиям: "click element [3]"
- Полный контроль над тем, что извлекается

**Минусы:**
- Пропускает контекст (текст вокруг элементов)
- Не видит содержимое страницы — только точки взаимодействия
- Нужно самостоятельно фильтровать невидимые элементы

### 3.3 Скриншот + Vision

**Суть:** Делаем скриншот страницы и отправляем в LLM с поддержкой vision. Модель "видит" страницу как пользователь.

**Когда лучше текста:**
- Страницы с тяжёлым визуальным контентом (карты, графики, дашборды)
- Когда DOM зашифрован или обфусцирован
- Для валидации: "правильно ли я заполнил форму?"
- CAPTCHA-подобные элементы
- Canvas-based приложения

**Ограничения:**
- Высокая стоимость токенов (скриншот 1080p ~1000–2000 tokens)
- Модель не может точно указать координаты клика
- Медленнее текстового подхода
- Не может "прочитать" скрытые элементы (dropdown options и т.д.)

### 3.4 Гибридный подход (рекомендуемый)

**Суть:** Основной источник — accessibility tree / DOM с ID. Скриншот — как дополнение для сложных случаев.

**Алгоритм:**
```
1. Получить accessibility tree → отправить в LLM
2. Если LLM не может определить нужное действие:
   a. Сделать скриншот
   b. Повторить запрос с tree + screenshot
3. Выполнить действие по ref/ID из дерева
4. После действия — снова snapshot для верификации
```

**Это именно тот подход, который используют:**
- **Playwright MCP** (snapshot по умолчанию, vision — опциональный режим через `--vision`)
- **browser-use** (HTML extraction + vision)
- **Stagehand** (accessibility tree через CDP + vision fallback)

### 3.5 Существующие решения для извлечения

| Решение | Подход | Примечание |
|---|---|---|
| **Playwright MCP** (`@playwright/mcp`) | Accessibility snapshot (YAML) + опциональный vision | Официальный от Microsoft. Лучшая интеграция с Claude/Copilot |
| **browser-use** | Кастомный DOM extraction (интерактивные элементы с ID) + vision | Самый популярный open-source агент (81K+ stars) |
| **Stagehand** | CDP-based accessibility tree + DOM snapshots | Перешли с Playwright на прямой CDP для скорости |
| **tappi** | CDP raw DOM, компактные indexed element lists | Минимальный расход токенов. Хорошо с shadow DOM |

### Рекомендация

Начать с **accessibility tree через Playwright** (`page.accessibility.snapshot()`) — это даёт наилучший баланс компактности и информативности. Для действий — использовать присвоенные ID/ref интерактивных элементов. Скриншот + vision — как fallback при ошибках или на визуально-сложных страницах.

---

## 4. Обзор open-source решений

### browser-use

- **Репо:** https://github.com/browser-use/browser-use
- **Язык:** Python
- **Stars:** 81K+ (март 2026)
- **Идея:** Универсальный AI-агент для управления браузером. Внедряет JS на страницу для извлечения интерактивных элементов, присваивает им ID, отправляет в LLM (любой через LangChain), получает действие, выполняет через Playwright.
- **LLM:** Любой через LangChain — Claude, GPT, Gemini, DeepSeek
- **Архитектура:** ReAct loop. Один агент получает состояние страницы + историю → решает следующее действие.
- **Особенности:** Недавно начали переход с Playwright на прямой CDP (`cdp-use` библиотека) для лучшей производительности и поддержки cross-origin iframes
- **Что взять:** Подход к DOM extraction, структуру промптов для агента, набор доступных действий (click, type, scroll, navigate, extract_content и т.д.). Также интересен `web-ui` — Gradio-интерфейс для взаимодействия с агентом

### Skyvern

- **Репо:** https://github.com/Skyvern-AI/skyvern
- **Язык:** Python (бэкенд) + TypeScript (SDK)
- **Идея:** Агент для RPA-задач (заполнение форм, логин, скачивание файлов). Использует "swarm of agents" — несколько специализированных агентов для разных аспектов задачи. Сильнейший на WRITE-задачах (формы, покупки).
- **Особенности:** Поддерживает workflows — цепочки задач. Может работать с Playwright actions напрямую + AI-augmented actions (например, `page.click(prompt="Add first item to cart")`)
- **Что взять:** Подход к разделению на sub-agents, Workflow-паттерн, обработку credentials

### HyperAgent

- **Репо:** https://github.com/hyperbrowserai/HyperAgent
- **Язык:** TypeScript
- **Идея:** TypeScript-first AI browser agent. Предоставляет API: `page.ai()` для высокоуровневых задач, `page.perform()` для конкретных действий, `page.extract()` для извлечения данных со schema-валидацией (через Zod).
- **Особенности:** Action Cache — записывает шаги и может воспроизводить их без LLM. Поддержка multi-page (несколько вкладок параллельно).
- **Что взять:** TypeScript API design, action cache для оптимизации, multi-page architecture

### Vercel Agent Browser

- **Репо:** https://github.com/vercel-labs/agent-browser
- **Язык:** TypeScript (CLI)
- **Идея:** CLI-инструмент для AI-агентов. Команды: `screenshot --annotate` (скриншот с пронумерованными элементами), `click @e2`, `type @e3 "text"`. Annotated screenshots — скриншот с наложенными ref-метками для vision-моделей.
- **Что взять:** Подход с annotated screenshots — полезен для гибридного метода (vision + refs)

### BrowserAgent (TIGER-AI-Lab)

- **Репо:** https://github.com/TIGER-AI-Lab/BrowserAgent
- **Язык:** Python
- **Идея:** Исследовательский проект (принят на TMLR 2025). Обучают модели через SFT + RFT на задачах веб-навигации. Используют human-inspired browsing actions.
- **Что взять:** Набор действий, имитирующих поведение человека; подход к обучению агента

---

## 5. Архитектурные паттерны

### 5.1 ReAct Loop (Reasoning + Acting)

Основной паттерн для AI browser agent. Цикл: наблюдение → размышление → действие → наблюдение нового состояния.

```
function react_loop(task):
    history = []
    
    while not task_completed:
        # 1. OBSERVE — получить состояние страницы
        page_state = get_accessibility_tree() + get_url() + get_title()
        
        # 2. REASON — LLM анализирует ситуацию
        prompt = f"""
        Task: {task}
        Current URL: {current_url}
        Page state: {page_state}
        Previous actions: {history[-5:]}  # последние 5 действий
        
        What should I do next? Think step by step, then choose an action.
        """
        
        response = llm.call(prompt, tools=available_actions)
        
        # 3. ACT — выполнить действие
        if response.has_tool_call:
            result = execute_action(response.tool_call)
            history.append({action, result})
        elif response.says_task_complete:
            return response.final_answer
        elif response.says_task_failed:
            return error
        
        # 4. VERIFY — проверить результат
        wait_for_navigation_or_change()
```

### 5.2 Tool Use Loop (через LLM API)

Конкретная реализация ReAct через tool use / function calling API.

```
function tool_use_loop(task):
    messages = [
        {role: "system", content: SYSTEM_PROMPT},
        {role: "user", content: task}
    ]
    
    while True:
        # Вызов LLM с инструментами
        response = llm.create(
            messages=messages,
            tools=[
                {name: "click", description: "Click element by ref ID", input_schema: {...}},
                {name: "type_text", description: "Type text into element", input_schema: {...}},
                {name: "navigate", description: "Go to URL", input_schema: {...}},
                {name: "get_page_state", description: "Get current page state", input_schema: {...}},
                {name: "scroll", description: "Scroll page up or down", input_schema: {...}},
                {name: "done", description: "Task is complete", input_schema: {...}},
            ]
        )
        
        # Добавить ответ ассистента
        messages.append(response.message)
        
        # Обработать tool calls
        if response.has_tool_calls:
            for tool_call in response.tool_calls:
                result = execute_tool(tool_call.name, tool_call.arguments)
                messages.append({
                    role: "tool",
                    tool_use_id: tool_call.id,
                    content: json.dumps(result)
                })
        elif response.stop_reason == "end_turn":
            return response.text  # задача завершена
```

### 5.3 Sub-Agent Architecture

Разделение на специализированных агентов для сложных задач.

```
# Planner Agent — высокоуровневое планирование
# Navigator Agent — навигация и поиск нужной страницы  
# Executor Agent — выполнение действий на странице
# Verifier Agent — проверка результатов

function sub_agent_architecture(task):
    # 1. Planner разбивает задачу на шаги
    plan = planner_agent.create_plan(task)
    # Пример: ["Открыть hh.ru", "Найти вакансию Python", "Открыть вакансию", "Нажать Откликнуться"]
    
    for step in plan:
        # 2. Navigator находит нужную страницу
        if step.requires_navigation:
            navigator_agent.navigate(step.target)
        
        # 3. Executor выполняет действия
        result = executor_agent.execute(step.action, page_state)
        
        # 4. Verifier проверяет результат
        if not verifier_agent.verify(step.expected_outcome, page_state):
            # Перепланирование
            plan = planner_agent.replan(task, completed_steps, error)
```

**Когда применять:**
- Задачи с 10+ шагами (планирование отдельно от исполнения)
- Разные модели для разных задач (дешёвая для навигации, умная для принятия решений)
- Нужна верификация результатов (отдельный агент-верификатор)

### 5.4 Context Window Management

Управление историей, чтобы не превысить лимит токенов. Критически важно для длинных сессий.

```
function manage_context(messages, max_tokens=100000):
    current_tokens = count_tokens(messages)
    
    if current_tokens < max_tokens * 0.7:
        return messages  # ещё есть запас
    
    # Стратегия 1: Sliding Window — сохранить системный промпт + последние N шагов
    system = messages[0]
    recent = messages[-10:]  # последние 10 сообщений
    return [system] + recent
    
    # Стратегия 2: Суммаризация — попросить LLM сжать историю
    summary = llm.summarize(messages[1:-5])
    return [system, {role: "user", content: f"Summary of previous actions: {summary}"}] + messages[-5:]
    
    # Стратегия 3: Compaction (поддерживается Claude Opus 4.6 и GPT-5.4 нативно)
    # Модель сама сжимает контекст, сохраняя ключевую информацию
    response = llm.create(messages, compaction=True)

    # Стратегия 4: Приоритизация
    # Всегда сохранять: системный промпт, текущее состояние страницы, последнее действие
    # Можно удалить: промежуточные состояния страниц, неуспешные действия
```

**Практические рекомендации:**
- Системный промпт с описанием tools: ~2–3K токенов (фиксированный)
- Состояние страницы (accessibility tree): 2–10K токенов (обновляется каждый шаг)
- История действий: растёт линейно, нужно ограничивать
- Бюджет на 1 шаг при 200K контексте: ~150K на историю, ~20K на текущее состояние, ~30K на ответ

---

## 6. Итоговые рекомендации

### Технологический стек

| Компонент | Выбор | Обоснование |
|---|---|---|
| **Язык** | **TypeScript** или **Python** | TypeScript — если важна скорость Playwright. Python — если хочется использовать browser-use как основу или LangChain. Оба варианта хороши |
| **Браузер** | **Playwright** | Лучший баланс: persistent context, accessibility tree, headed mode, CDP доступ, multi-browser |
| **LLM** | **Claude Sonnet 4.5** (основной) | $3/$15, отличный tool use, vision, 200K контекст. Prompt caching для экономии |
| **LLM (бюджетный)** | **GPT-4.1** или **GPT-4.1 mini** | $2/$8, оптимизирован для function calling, 1M контекст |
| **Извлечение страницы** | **Accessibility tree + interactive elements с ID** | Компактно, информативно, позволяет точные действия |
| **Fallback** | **Screenshot + Vision** | Для визуально-сложных страниц и верификации |

### Архитектура

1. **Основной цикл:** Tool Use Loop через Claude/OpenAI API
2. **Продвинутый паттерн:** Security Layer — подтверждение деструктивных действий (покупки, удаления, отправки) перед выполнением
3. **Context management:** Sliding window (последние 10–15 шагов) + суммаризация старых шагов
4. **Error handling:** Retry с адаптацией — если действие не привело к изменению, попробовать другой подход (например, переключиться с accessibility tree на screenshot)

### Что позаимствовать из open-source

- **browser-use:** Набор действий (click, type, scroll, go_to_url, extract_content, done), подход к DOM extraction, структуру промптов
- **Playwright MCP:** YAML accessibility snapshot формат, ref-идентификаторы для элементов
- **Skyvern:** Sub-agent подход для workflow, обработку credentials
- **Vercel Agent Browser:** Annotated screenshots (скриншот + наложенные ref-метки)

### Примерный поток работы агента

```
Пользователь: "Найди вакансию Python-разработчика на hh.ru и откликнись"

1. [navigate] → hh.ru
2. [get_page_state] → accessibility tree страницы
3. [type_text] ref=search_input, text="Python-разработчик"
4. [click] ref=search_button
5. [get_page_state] → список вакансий
6. [click] ref=first_vacancy_link
7. [get_page_state] → страница вакансии
8. [SECURITY CHECK] → "Агент хочет откликнуться на вакансию 'Python Developer в Company X'. Подтвердить? [y/n]"
9. [click] ref=apply_button
10. [done] → "Отклик на вакансию отправлен"
```
