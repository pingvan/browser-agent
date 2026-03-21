# AI Browser Agent — Архитектура и план реализации

---

## Часть 1: Финальный стек технологий

### 1. Язык программирования

**Выбор:** Python 3.12+ (с type hints, `asyncio`)

**Почему:** Playwright имеет полноценные async Python-биндинги (`playwright.async_api`). Экосистема Python богата для работы с AI — прямой `openai` SDK, удобная работа с JSON. Быстрый цикл разработки без этапа компиляции.

**Отвергнуто:**
- TypeScript — хорошие Playwright-биндинги, но Python выбран как обязательное требование проекта.

**Риски:**
- Async Python менее привычен, чем sync → **Митигация:** весь код через `async/await`, единый event loop через `asyncio.run()`.
- Отсутствие строгой типизации → **Митигация:** используем type hints повсеместно + `TypedDict` / `dataclass` для структур данных.

---

### 2. Библиотека автоматизации браузера

**Выбор:** Playwright for Python `^1.50.0` (пакет `playwright`)

**Почему:** `browser.launch_persistent_context()` — нативная поддержка persistent sessions с сохранением cookies и авторизации. Встроенный `page.accessibility.snapshot()` для получения accessibility tree. Стабильный headed-режим с Chromium.

**Отвергнуто:**
- Puppeteer — нет Python-биндингов.
- Selenium — нет нативного accessibility tree, verbose API, избыточен для этого проекта.
- Прямой CDP — слишком низкоуровневый для MVP.

**Риски:**
- Accessibility tree может быть неполным на SPA с Shadow DOM → **Митигация:** основной метод — custom DOM extraction через `page.evaluate()`, accessibility tree как дополнение.
- Playwright обновляется часто → **Митигация:** зафиксировать версию в `pyproject.toml`, точные версии в `uv.lock`.

---

### 3. AI-провайдер и модель

**Выбор:** OpenAI GPT-4.1 (`gpt-4.1`) через `openai` Python SDK

**Почему:** GPT-4.1 специально оптимизирован для tool calling — лидер бенчмарков по function calling. $2/$8 за 1M токенов — дешевле Claude Sonnet при сопоставимом качестве. 1M контекстное окно — огромный запас для длинных агентных сессий. Автоматический prompt caching со скидкой на cached input.

**Альтернатива для сложных задач:** GPT-4o (`gpt-4o`) — если GPT-4.1 не справляется с reasoning.

**Отвергнуто:**
- Claude Sonnet — хороший tool use, но OpenAI выбран как требование проекта.
- GPT-4.1 mini — дешевле ($0.4/$1.6), но слабее на сложных multi-step задачах; можно использовать для простых подзадач.
- GPT-4.1 nano — слишком слабая для агентных сценариев.

**Риски:**
- Rate limits при активной разработке → **Митигация:** exponential backoff с retry; prompt caching снижает costs.
- Модель может галлюцинировать несуществующие элементы → **Митигация:** валидация tool call параметров перед выполнением (проверяем, что ref существует на странице).

---

### 4. Способ извлечения информации со страницы

**Выбор:** Гибридный подход — DOM Extraction интерактивных элементов (основной) + текстовое содержимое (контекст) + Screenshot/Vision (fallback)

**Алгоритм:**
1. `page.evaluate()` → собираем интерактивные элементы, присваиваем числовые `[ref=N]` ID
2. `page.evaluate()` → собираем видимый текст страницы (ограниченно)
3. Объединяем в единый текстовый формат для LLM
4. Если LLM возвращает ошибку или не может найти элемент → делаем `page.screenshot()` и повторяем с vision

**Отвергнуто:**
- Только accessibility tree — пропускает кастомные элементы без ARIA-разметки.
- Только screenshot + vision — дорого по токенам (~1500 tokens за скриншот), модель не может точно указать элемент для клика.
- Полный HTML — слишком объёмный (50–200K токенов на типичной странице).

**Риски:**
- DOM extraction пропускает элементы в Shadow DOM → **Митигация:** добавляем `querySelectorAll` через `shadowRoot` traversal при необходимости.
- Слишком много элементов на сложных страницах → **Митигация:** ограничение 150 элементов + приоритет видимых в viewport.

---

### 5. Формат CLI

**Выбор:** Встроенный `asyncio`-совместимый readline через `aioconsole` + `colorama` для цветного вывода

**Почему:** `aioconsole` даёт `async input()` — не блокирует event loop. `colorama` — кроссплатформенные ANSI-цвета. Минимум зависимостей.

**Альтернатива:** `prompt_toolkit` — если понадобится autocomplete или сложный TUI. Для MVP избыточен.

**Конкретный UX:**
```
🤖 AI Browser Agent ready. Browser launched.
📍 Current page: about:blank

> Найди на hh.ru вакансии Python-разработчика в Москве

🔄 Step 1: navigate → hh.ru
🔄 Step 2: type_text → "Python-разработчик"
⚠️  SECURITY: Agent wants to submit a search form on hh.ru. Allow? (y/n): y
🔄 Step 3: click → "Найти работу"
✅ Done: Found 234 vacancies. Opened the first one.

> _
```

---

### 6. Структура проекта

**Выбор:** Плоский Python-пакет в `src/`. Управление зависимостями через `uv` (`pyproject.toml` + `uv.lock`). Запуск через `uv run python -m src.main`.

**Почему:** Проект достаточно мал для плоской структуры. Модульность через отдельные `.py` файлы по ответственности.

---

### 7. Линтер и форматирование

**Выбор:** Ruff (пакет `ruff`, dev-зависимость)

**Почему:** Один инструмент заменяет flake8 + isort + black + pyupgrade. Написан на Rust — проверяет весь проект за миллисекунды. Стандарт де-факто для новых Python-проектов в 2025–2026.

**Отвергнуто:**
- flake8 + black + isort — три инструмента вместо одного, медленнее, нужна отдельная конфигурация каждого.
- pylint — слишком агрессивный для MVP, много false positives, медленный.

**Конфигурация в `pyproject.toml`:**
```toml
[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = [
    "E",     # pycodestyle errors
    "W",     # pycodestyle warnings
    "F",     # pyflakes
    "I",     # isort
    "UP",    # pyupgrade (modern Python syntax)
    "ASYNC", # async antipatterns
]
ignore = [
    "E501",  # line too long — ruff formatter handles this
]

[tool.ruff.format]
quote-style = "double"
```

**Команды:**
```bash
uv run ruff check src/          # линтинг
uv run ruff check src/ --fix    # автоисправление
uv run ruff format src/         # форматирование
```

---

### 8. GitHub Workflows (CI)

**Выбор:** Два workflow — `ci.yml` (на каждый push/PR) и `demo.yml` (ручной запуск).

**CI pipeline (`ci.yml`):** Линтинг + форматирование + type checking. Быстрый — выполняется за ~30 секунд. Не включает запуск агента (требует API-ключи и браузер).

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync --dev

      - name: Ruff lint
        run: uv run ruff check src/

      - name: Ruff format check
        run: uv run ruff format src/ --check

      - name: Type check
        run: uv run pyright src/
```

**Почему pyright для type checking:** Быстрее mypy, лучше работает с async-кодом и `openai` SDK (который сам написан с учётом pyright). Запускается без дополнительной конфигурации.

**Что НЕ включаем в CI и почему:**
- Запуск агента — требует `OPENAI_API_KEY`, headed-браузер, доступ к интернету. Не подходит для CI.
- E2E-тесты — проект не имеет тестов в MVP (scope ограничен). При масштабировании — добавить `pytest` с мок-сервером.

---

## Часть 2: Архитектура системы

### Модуль 1: Browser Controller (`src/browser/`)

#### Запуск persistent context

```python
from playwright.async_api import async_playwright, BrowserContext, Page
import os

USER_DATA_DIR = os.path.join(os.getcwd(), ".browser-data")

async def launch_browser() -> tuple[BrowserContext, Page]:
    playwright = await async_playwright().start()

    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=False,
        viewport={"width": 1280, "height": 900},
        locale="ru-RU",
        args=[
            "--disable-blink-features=AutomationControlled",  # скрываем автоматизацию
        ],
    )

    # Берём первую открытую страницу или создаём новую
    page = context.pages[0] if context.pages else await context.new_page()

    # Обработка диалогов (alert, confirm, prompt)
    page.on("dialog", lambda dialog: dialog.accept())

    # Обработка новых вкладок
    context.on("page", lambda new_page: print(f"[New Tab] {new_page.url}"))

    return context, page
```

#### Стратегия ожидания загрузки страницы

Ключевая проблема: `waitUntil='domcontentloaded'` покрывает только парсинг HTML. SPA-приложения (React, Vue, Angular) рендерят контент через JS после этого события. `networkidle` зависает на сайтах с websocket/analytics/long-polling.

**Решение — трёхуровневый wait:**

```python
async def wait_for_page_ready(page: Page, timeout: int = 10000) -> None:
    """
    Уровень 1: domcontentloaded — HTML распарсен.
    Уровень 2: DOM stability — контент перестал меняться (SPA отрисовал).
    Уровень 3: Fallback timeout — если DOM нестабилен (бесконечные анимации).
    """

    # Уровень 1: базовая загрузка
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass  # продолжаем даже если timeout — страница может быть частично загружена

    # Уровень 2: ждём стабилизацию DOM
    # Проверяем длину innerHTML каждые 200мс.
    # Если 3 проверки подряд одинаковые — DOM стабилен.
    try:
        await page.evaluate("""
            () => new Promise((resolve) => {
                let lastSize = document.body.innerHTML.length;
                let stableCount = 0;
                const interval = setInterval(() => {
                    const currentSize = document.body.innerHTML.length;
                    if (currentSize === lastSize) {
                        stableCount++;
                        if (stableCount >= 3) {
                            clearInterval(interval);
                            resolve(true);
                        }
                    } else {
                        stableCount = 0;
                        lastSize = currentSize;
                    }
                }, 200);
                // Fallback: не ждём больше 5 секунд
                setTimeout(() => { clearInterval(interval); resolve(false); }, 5000);
            })
        """)
    except Exception:
        pass  # не критично

    # Уровень 3: финальная пауза для рендера (CSS transitions, lazy images)
    await page.wait_for_timeout(300)
```

**Когда вызывается:**
- После `page.goto()` (navigate tool)
- После `click()`, который вызвал навигацию (проверяем через `page.url` до и после)
- После `go_back()`

**Что НЕ покрывается и почему это ОК:**
- Бесконечный скролл (контент подгружается при скролле) → агент вызовет `scroll` + `get_page_state` сам
- Модальные окна с задержкой → агент увидит их при следующем `get_page_state`
- AJAX после действий пользователя → DOM stability check подхватит

#### Полный список Tools (действий агента)

**1. `navigate`**
```
Название: navigate
Описание: Navigate to a specified URL
Параметры: { url: str }
Возврат: { success: bool, url: str, title: str }
Логика: page.goto(url) → wait_for_page_ready()
```

**2. `click`**
```
Название: click
Описание: Click on an element identified by its ref number from the page state
Параметры: { ref: int }
Возврат: { success: bool, description: str }
Логика: Найти элемент по ref → element.click() → wait_for_page_ready() если URL изменился
```

**3. `type_text`**
```
Название: type_text
Описание: Type text into an input field. Clears existing content first.
Параметры: { ref: int, text: str, press_enter: bool = False }
Возврат: { success: bool, description: str }
Логика: element.fill(text), затем если press_enter — page.keyboard.press("Enter") → wait_for_page_ready()
```

**4. `select_option`**
```
Название: select_option
Описание: Select an option from a dropdown <select> element
Параметры: { ref: int, value: str }
Возврат: { success: bool, description: str }
Логика: element.select_option(value)
```

**5. `scroll`**
```
Название: scroll
Описание: Scroll the page up or down
Параметры: { direction: "up" | "down", amount: int = 500 }
Возврат: { success: bool, scroll_y: int }
Логика: page.evaluate(f"window.scrollBy(0, {±amount})")
```

**6. `get_page_state`**
```
Название: get_page_state
Описание: Get current page state including URL, title, and interactive elements. Call this after navigation or when you need to see what's on the page.
Параметры: {}
Возврат: { url: str, title: str, content: str }
Логика: Вызывает Page Parser (Модуль 2)
```

**7. `screenshot`**
```
Название: screenshot
Описание: Take a screenshot of the current page. Use when you need to visually inspect the page.
Параметры: {}
Возврат: base64-encoded JPEG image
Логика: page.screenshot(type="jpeg", quality=75, full_page=False)
```

**8. `go_back`**
```
Название: go_back
Описание: Go back to the previous page in browser history
Параметры: {}
Возврат: { success: bool, url: str }
Логика: page.go_back() → wait_for_page_ready()
```

**9. `get_tabs`**
```
Название: get_tabs
Описание: List all open browser tabs
Параметры: {}
Возврат: { tabs: list[{ index: int, url: str, title: str, active: bool }] }
Логика: context.pages → map
```

**10. `switch_tab`**
```
Название: switch_tab
Описание: Switch to a different browser tab by index
Параметры: { index: int }
Возврат: { success: bool, url: str, title: str }
Логика: page = context.pages[index]; page.bring_to_front()
```

**11. `press_key`**
```
Название: press_key
Описание: Press a keyboard key or combination (e.g., "Enter", "Escape", "Control+a")
Параметры: { key: str }
Возврат: { success: bool }
Логика: page.keyboard.press(key)
```

**12. `hover`**
```
Название: hover
Описание: Hover over an element to reveal tooltips or dropdown menus
Параметры: { ref: int }
Возврат: { success: bool, description: str }
Логика: element.hover()
```

**13. `done`**
```
Название: done
Описание: Call this when the task is complete. Provide a summary of what was accomplished.
Параметры: { summary: str, success: bool }
Возврат: — (завершает цикл)
```

#### Обработка попапов, алертов, новых вкладок

```python
# Алерты: принимаем по умолчанию, сохраняем в историю
dialog_history: list[dict] = []

async def handle_dialog(dialog):
    dialog_history.append({"type": dialog.type, "message": dialog.message})
    await dialog.accept()

page.on("dialog", handle_dialog)

# Новые вкладки: добавляем в список, агент сам решает через get_tabs/switch_tab
context.on("page", lambda p: p.once("domcontentloaded", lambda: print(f"[New Tab] {p.url}")))

# File download:
async def handle_download(download):
    path = os.path.join("./downloads", download.suggested_filename)
    await download.save_as(path)
    print(f"[Download] {path}")

page.on("download", handle_download)
```

---

### Модуль 2: Page Parser / Context Extractor (`src/parser/`)

#### Алгоритм извлечения

```python
from dataclasses import dataclass

@dataclass
class InteractiveElement:
    ref: int
    tag: str
    role: str
    text: str
    aria_label: str
    placeholder: str
    href: str
    input_type: str
    value: str
    disabled: bool

@dataclass
class PageState:
    url: str
    title: str
    content: str              # форматированная текстовая карта для LLM
    elements: list[InteractiveElement]  # для маппинга ref → элемент

async def extract_page_state(page: Page) -> PageState:
    url = page.url
    title = await page.title()

    # 1. Собираем интерактивные элементы через JS-инъекцию
    elements_raw = await page.evaluate("""
    () => {
        const SELECTORS = [
            'a[href]', 'button', 'input', 'select', 'textarea',
            '[role="button"]', '[role="link"]', '[role="tab"]',
            '[role="menuitem"]', '[role="checkbox"]', '[role="radio"]',
            '[onclick]', '[tabindex]:not([tabindex="-1"])',
            'summary', 'details'
        ].join(', ');

        const els = document.querySelectorAll(SELECTORS);
        const results = [];

        els.forEach((el) => {
            const rect = el.getBoundingClientRect();
            // Пропускаем невидимые
            if (rect.width === 0 || rect.height === 0) return;
            if (window.getComputedStyle(el).visibility === 'hidden') return;
            if (window.getComputedStyle(el).display === 'none') return;
            // Пропускаем элементы далеко за viewport
            if (rect.top > window.innerHeight + 500) return;
            if (rect.bottom < -500) return;

            const ref = results.length;
            el.setAttribute('data-agent-ref', String(ref));

            results.push({
                ref: ref,
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                text: (el.textContent || '').trim().slice(0, 80),
                ariaLabel: el.getAttribute('aria-label') || '',
                placeholder: el.placeholder || '',
                href: el.href ? el.href.slice(0, 120) : '',
                type: el.type || '',
                value: (el.value || '').slice(0, 50),
                disabled: el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true',
            });
        });

        return results;
    }
    """)

    # 2. Собираем текстовое содержимое страницы
    text_content = await page.evaluate("""
    () => {
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT,
            {
                acceptNode: (node) => {
                    const parent = node.parentElement;
                    if (!parent) return NodeFilter.FILTER_REJECT;
                    const tag = parent.tagName.toLowerCase();
                    if (['script', 'style', 'noscript', 'svg'].includes(tag))
                        return NodeFilter.FILTER_REJECT;
                    if (parent.offsetParent === null && tag !== 'body')
                        return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }
            }
        );
        const texts = [];
        let total = 0;
        while (walker.nextNode()) {
            const t = (walker.currentNode.textContent || '').trim();
            if (t.length > 2) {
                texts.push(t);
                total += t.length;
                if (total > 4000) break;
            }
        }
        return texts.join(' | ');
    }
    """)

    elements = [
        InteractiveElement(
            ref=e["ref"], tag=e["tag"], role=e["role"],
            text=e["text"], aria_label=e["ariaLabel"],
            placeholder=e["placeholder"], href=e["href"],
            input_type=e["type"], value=e["value"], disabled=e["disabled"],
        )
        for e in elements_raw
    ]

    content = format_page_state(url, title, elements, text_content)
    return PageState(url=url, title=title, content=content, elements=elements)
```

#### Формат вывода для LLM

```python
def format_page_state(
    url: str,
    title: str,
    elements: list[InteractiveElement],
    text_content: str,
    max_elements: int = 150,
) -> str:
    lines = []
    lines.append(f"## Current Page")
    lines.append(f"URL: {url}")
    lines.append(f"Title: {title}")
    lines.append("")

    if text_content:
        lines.append("## Page Content (summary)")
        lines.append(text_content[:2000])
        lines.append("")

    lines.append("## Interactive Elements")
    for el in elements[:max_elements]:
        desc = f"[{el.ref}]"

        # Тип элемента
        if el.role:
            desc += f" {el.role}"
        elif el.tag == "a":
            desc += " link"
        elif el.tag == "button":
            desc += " button"
        elif el.tag == "input":
            desc += f" input[{el.input_type or 'text'}]"
        elif el.tag == "select":
            desc += " select"
        elif el.tag == "textarea":
            desc += " textarea"
        else:
            desc += f" {el.tag}"

        label = el.aria_label or el.text or el.placeholder
        if label:
            desc += f' "{label}"'

        if el.href:
            desc += f" → {el.href}"
        if el.value:
            desc += f' value="{el.value}"'
        if el.disabled:
            desc += " [disabled]"

        lines.append(desc)

    if len(elements) > max_elements:
        lines.append(f"... and {len(elements) - max_elements} more elements (scrolled out of view)")

    return "\n".join(lines)
```

#### Ограничение по токенам

- **Максимум на состояние страницы:** ~8000 токенов (~32K символов)
- **Если превышает:** обрезаем `text_content` до 1000 символов, затем элементы до 100 штук
- **Приоритет сохранения:** интерактивные элементы в viewport > текстовый контекст > элементы за пределами viewport
- **Оценка токенов:** `tiktoken` для точного подсчёта GPT-4.1 токенов (модель `cl100k_base`)

#### Пример: главная страница Google

```
## Current Page
URL: https://www.google.com/
Title: Google

## Page Content (summary)
Google | Поиск в Google | Мне повезёт | Gmail | Картинки | Реклама | Бизнес | Как работает Google Поиск | Конфиденциальность | Условия

## Interactive Elements
[0] link "Gmail" → https://mail.google.com/
[1] link "Картинки" → https://www.google.com/imghp
[2] button "Приложения Google"
[3] link "Войти" → https://accounts.google.com/
[4] textarea "Поиск" value=""
[5] button "Поиск в Google"
[6] button "Мне повезёт"
[7] link "Реклама" → https://ads.google.com/
[8] link "Бизнес" → https://www.google.com/intl/ru/about/products/
[9] link "Как работает Google Поиск" → https://www.google.com/search/howsearchworks/
[10] link "Конфиденциальность" → https://policies.google.com/privacy
[11] link "Условия" → https://policies.google.com/terms
```

---

### Модуль 3: Agent Core / LLM Loop (`src/agent/`)

#### System Prompt (полный текст)

```python
SYSTEM_PROMPT = """You are an AI browser agent that controls a web browser to accomplish tasks given by the user. You interact with web pages by calling tools that perform browser actions.

## How you work

1. You receive a task from the user.
2. You observe the current page state (URL, title, interactive elements).
3. You decide which action to take next.
4. You call the appropriate tool.
5. You observe the result and the new page state.
6. You repeat until the task is complete.

## Important rules

- ALWAYS call get_page_state first to see what's on the page before taking action.
- Use element ref numbers (e.g., [3]) from the page state to identify which element to click/type into.
- After clicking a link or submitting a form, call get_page_state again to see the new page.
- If you can't find an element, try scrolling down to reveal more content.
- If an action fails, try an alternative approach (e.g., use keyboard navigation, or navigate via URL).
- When the task is complete, call the "done" tool with a summary.
- Be precise — don't guess ref numbers. Always use refs from the latest page state.
- For search tasks: type the query AND press Enter or click the search button.
- If a page takes too long to respond, try refreshing or navigating directly via URL.

## When to use screenshot

Call the screenshot tool when:
- You're unsure about the visual layout of the page
- The page state seems incomplete or confusing
- You need to verify that an action was performed correctly
- You're dealing with a complex visual interface (maps, charts, etc.)

## Safety

Some actions are sensitive (purchases, form submissions, account changes, sending messages, deleting data). The system will ask the user for confirmation before executing these. Do not try to bypass this — it's a safety feature.

## Language

Communicate in the same language as the user's task. If the task is in Russian, think and respond in Russian."""
```

#### Tool definitions для OpenAI API

```python
from openai import AsyncOpenAI

client = AsyncOpenAI()  # Берёт OPENAI_API_KEY из env

tools = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Navigate to a specified URL in the browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to navigate to. Must include protocol (https://)."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click on an interactive element identified by its ref number from the page state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "integer", "description": "The ref number of the element to click (from page state)."}
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into an input field. Clears existing content first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "integer", "description": "The ref number of the input element."},
                    "text": {"type": "string", "description": "The text to type."},
                    "press_enter": {"type": "boolean", "description": "Whether to press Enter after typing. Default: false."},
                },
                "required": ["ref", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_option",
            "description": "Select an option from a dropdown <select> element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "integer", "description": "The ref number of the select element."},
                    "value": {"type": "string", "description": "The value or visible text of the option to select."},
                },
                "required": ["ref", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the page up or down to reveal more content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction."},
                    "amount": {"type": "integer", "description": "Pixels to scroll. Default: 500."},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_state",
            "description": "Get current page state: URL, title, visible text, and interactive elements with ref numbers. Call this after any navigation or when you need to see the page.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Take a screenshot of the visible area. Use when you need to visually inspect the page layout, verify results, or when page state seems incomplete.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "go_back",
            "description": "Navigate back to the previous page in browser history.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tabs",
            "description": "List all open browser tabs with their URLs and titles.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_tab",
            "description": "Switch to a different browser tab by its index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Tab index (from get_tabs)."}
                },
                "required": ["index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": 'Press a key or key combination (e.g., "Enter", "Escape", "Tab", "Control+a").',
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name or combination."}
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hover",
            "description": "Hover over an element to reveal tooltips or dropdown menus.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "integer", "description": "The ref number of the element to hover over."}
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Call this when the task is complete or when you determine it cannot be completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Summary of what was accomplished."},
                    "success": {"type": "boolean", "description": "Whether the task was completed successfully."},
                },
                "required": ["summary", "success"],
            },
        },
    },
]
```

#### Основной цикл агента

```python
import json
import base64

async def run_agent(task: str, page: Page, context: BrowserContext) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Task: {task}\n\nStart by calling get_page_state to see the current page."},
    ]

    MAX_STEPS = 50
    step = 0
    loop_detector = LoopDetector()

    while step < MAX_STEPS:
        step += 1

        # Управление контекстом (Модуль 4)
        managed_messages = context_manager.prepare(messages)

        # Вызов LLM
        response = await client.chat.completions.create(
            model="gpt-4.1",
            messages=managed_messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=4096,
        )

        choice = response.choices[0]
        message = choice.message

        # Добавляем ответ в историю
        messages.append(message.model_dump())

        # Обрабатываем tool calls
        if message.tool_calls:
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)

                print(f"🔄 Step {step}: {fn_name}({json.dumps(fn_args, ensure_ascii=False)})")
                loop_detector.record_action(fn_name, fn_args)

                # Проверка безопасности (Модуль 5)
                if security_layer.is_dangerous(fn_name, fn_args, current_page_state):
                    allowed = await security_layer.request_confirmation(fn_name, fn_args)
                    if not allowed:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": "Action was rejected by the user. Try a different approach or ask the user for guidance.",
                        })
                        continue

                # Выполнение tool
                result = await error_recovery.execute_with_retry(
                    lambda: execute_tool(fn_name, fn_args, page, context)
                )

                # Screenshot → отправляем как image_url
                if fn_name == "screenshot" and isinstance(result, dict) and "base64_image" in result:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{result['base64_image']}",
                                    "detail": "low",  # экономия токенов
                                },
                            },
                        ],
                    })
                else:
                    # Подсказка при зацикливании
                    content = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                    if loop_detector.is_stuck():
                        content += "\n\n⚠️ " + loop_detector.get_unstuck_hint()

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": content,
                    })

                # done → завершаем
                if fn_name == "done":
                    summary = fn_args.get("summary", "Task finished.")
                    success = fn_args.get("success", True)
                    icon = "✅" if success else "❌"
                    print(f"{icon} {summary}")
                    return summary

        elif choice.finish_reason == "stop":
            # LLM ответил текстом без tool calls
            text = message.content or ""
            print(f"💬 Agent: {text}")
            return text

    return "Max steps reached. Task may be incomplete."
```

#### Условия завершения цикла

1. **Успешное завершение:** Агент вызывает `done` с `success=True`.
2. **Неуспешное завершение:** Агент вызывает `done` с `success=False`.
3. **Лимит шагов:** Превышено `MAX_STEPS=50` → принудительное завершение.
4. **Ответ без tool calls:** `finish_reason == "stop"` — агент хочет сообщить что-то пользователю.

#### Обнаружение зацикливания

```python
class LoopDetector:
    def __init__(self):
        self.action_history: list[str] = []

    def record_action(self, name: str, args: dict) -> None:
        signature = f"{name}:{json.dumps(args, sort_keys=True)}"
        self.action_history.append(signature)

    def is_stuck(self) -> bool:
        if len(self.action_history) < 4:
            return False
        last4 = self.action_history[-4:]
        # Повторяющиеся пары: ABAB
        if last4[0] == last4[2] and last4[1] == last4[3]:
            return True
        # Три одинаковых подряд: AAA
        last3 = self.action_history[-3:]
        if last3[0] == last3[1] == last3[2]:
            return True
        return False

    def get_unstuck_hint(self) -> str:
        return (
            "You seem to be repeating the same actions without progress. "
            "Try a completely different approach: navigate to a different page, "
            "use a different search query, or scroll to find other elements."
        )
```

---

### Модуль 4: Context Manager (`src/agent/context_manager.py`)

#### Стратегия управления контекстом

```python
class ContextManager:
    MAX_MESSAGES = 40          # ~20 шагов (assistant + tool на каждый)
    PAGE_STATE_BUDGET = 32_000  # символов на состояние страницы (~8K токенов)

    def prepare(self, messages: list[dict]) -> list[dict]:
        if len(messages) <= self.MAX_MESSAGES:
            return messages

        # Sliding Window: system prompt + первое user сообщение (задача) + последние N
        system_msg = messages[0]   # system prompt
        task_msg = messages[1]     # user task
        recent = messages[-self.MAX_MESSAGES:]

        # Краткая заметка об удалённом контексте
        removed_count = len(messages) - self.MAX_MESSAGES - 2
        context_note = {
            "role": "user",
            "content": (
                f"[Context note: {removed_count} earlier messages were trimmed. "
                "The task and recent actions are preserved. Continue from the current state.]"
            ),
        }

        return [system_msg, task_msg, context_note] + recent

    def truncate_page_state(self, content: str) -> str:
        """Обрезает состояние страницы, если оно слишком большое."""
        if len(content) <= self.PAGE_STATE_BUDGET:
            return content

        sections = content.split("## ")
        header = sections[0]
        elements_section = next((s for s in sections if s.startswith("Interactive Elements")), "")
        text_section = next((s for s in sections if s.startswith("Page Content")), "")

        result = header
        if elements_section:
            lines = elements_section.split("\n")
            result += "## " + "\n".join(lines[:101])  # 100 элементов
        if text_section and len(result) < self.PAGE_STATE_BUDGET - 1000:
            remaining = self.PAGE_STATE_BUDGET - len(result) - 100
            result += "\n## " + text_section[:remaining]

        return result
```

#### Что сохранять vs что можно удалить

| Приоритет | Данные | Логика |
|-----------|--------|--------|
| 🔴 Всегда | System prompt | Фиксированный, ~600 токенов |
| 🔴 Всегда | Первое сообщение (задача) | Нужно для контекста |
| 🔴 Всегда | Последний page state | Нужен для принятия решения |
| 🟡 Высокий | Последние 10 пар assistant+tool | Недавний контекст |
| 🟢 Средний | Ранние успешные шаги | Заменяются context note |
| ⚪ Низкий | Ранние page state | Устарели |
| ⚪ Первыми удалить | Скриншоты | Самый большой расход токенов |

---

### Модуль 5: Security Layer (`src/security/`)

#### Категории опасных действий

```python
import re
from dataclasses import dataclass

@dataclass
class SecurityRule:
    pattern: str           # regex паттерн
    category: str
    confirm_message: str

DANGEROUS_PATTERNS = [
    SecurityRule(
        pattern=r"buy|purchase|pay|checkout|order|оплат|купить|заказ|корзин|оформ",
        category="purchase",
        confirm_message="Agent wants to make a purchase/payment",
    ),
    SecurityRule(
        pattern=r"send|submit|post|publish|отправ|опублик|послать",
        category="communication",
        confirm_message="Agent wants to send/submit a message or form",
    ),
    SecurityRule(
        pattern=r"delete|remove|unsubscribe|cancel|удалить|отмен|отписа",
        category="deletion",
        confirm_message="Agent wants to delete or cancel something",
    ),
    SecurityRule(
        pattern=r"password|email.change|account.settings|пароль|настройки.акк",
        category="account",
        confirm_message="Agent wants to modify account settings",
    ),
    SecurityRule(
        pattern=r"subscribe|sign.up|register|подписа|регистра|зарегистр",
        category="subscription",
        confirm_message="Agent wants to subscribe or create an account",
    ),
]
```

#### Как определяем опасность: эвристики

```python
class SecurityLayer:
    def is_dangerous(self, tool_name: str, args: dict, page_state: PageState | None) -> bool:
        # 1. Эвристика по тексту элемента
        if tool_name in ("click", "type_text", "hover") and page_state:
            ref = args.get("ref")
            element = next((e for e in page_state.elements if e.ref == ref), None)
            if element:
                element_text = f"{element.text} {element.aria_label}".lower()
                for rule in DANGEROUS_PATTERNS:
                    if re.search(rule.pattern, element_text, re.IGNORECASE):
                        return True

        # 2. Навигация на платёжные страницы
        if tool_name == "navigate":
            url = (args.get("url") or "").lower()
            if re.search(r"checkout|payment|billing|pay\.", url):
                return True

        # 3. Отправка формы
        if tool_name == "type_text" and args.get("press_enter"):
            return True

        return False

    async def request_confirmation(self, tool_name: str, args: dict) -> bool:
        desc = self._describe_action(tool_name, args)
        answer = await async_input(f"\n⚠️  SECURITY: {desc}\n   Allow this action? (y/n): ")
        return answer.strip().lower().startswith("y")

    def _describe_action(self, tool_name: str, args: dict) -> str:
        if tool_name == "click":
            return f"Click on element [{args.get('ref')}]"
        elif tool_name == "type_text":
            return f'Type "{args.get("text")}" and submit'
        elif tool_name == "navigate":
            return f"Navigate to {args.get('url')}"
        return f"{tool_name}({json.dumps(args)})"
```

#### Что происходит при отклонении

1. Tool result → `"Action was rejected by the user. Try a different approach or ask the user for guidance."`
2. LLM адаптируется — пробует альтернативный путь или сообщает о невозможности.
3. Действие НЕ выполняется, страница не меняется.

---

### Модуль 6: Error Recovery (`src/utils/error_recovery.py`)

#### Типы ошибок и стратегии

```python
class ErrorRecovery:
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.retry_count = 0

    async def execute_with_retry(self, fn) -> dict:
        try:
            result = await fn()
            self.retry_count = 0
            return result
        except Exception as error:
            self.retry_count += 1
            if self.retry_count > self.max_retries:
                self.retry_count = 0
                return {
                    "success": False,
                    "error": f"Action failed after {self.max_retries} retries: {error}. Try a completely different approach.",
                }
            return self._handle_error(error)

    def _handle_error(self, error: Exception) -> dict:
        msg = str(error)

        # Element not found / detached
        if re.search(r"element.*not found|no element|detached", msg, re.I):
            return {
                "success": False,
                "error": "Element not found. The page may have changed. Call get_page_state to refresh your view of the page.",
            }

        # Timeout
        if re.search(r"timeout|timed out", msg, re.I):
            return {
                "success": False,
                "error": "Action timed out. The page may be loading slowly. Wait a moment and try again, or try navigating directly via URL.",
            }

        # Navigation failed
        if re.search(r"net::ERR_|navigation", msg, re.I):
            return {
                "success": False,
                "error": f"Navigation failed: {msg}. Check the URL and try again.",
            }

        # Page crash
        if re.search(r"crash|target closed", msg, re.I):
            return {
                "success": False,
                "error": "Page crashed. Try navigating to the URL again.",
            }

        # Default
        return {
            "success": False,
            "error": f"Action failed: {msg}. Try a different approach.",
        }
```

#### Ключевой принцип

Ошибки **не выбрасываются** — они конвертируются в текстовый feedback для LLM. Модель получает описание ошибки и адаптирует стратегию:

```
Tool result → "Element [15] not found. Call get_page_state to refresh."
LLM → вызывает get_page_state → видит обновлённую страницу → находит правильный элемент
```

---

## Часть 3: Диаграмма взаимодействия

### Задача: "Найди на hh.ru вакансии Python-разработчика в Москве и открой первую из списка"

#### Шаг 0: Инициализация
```
User → CLI: "Найди на hh.ru вакансии Python-разработчика в Москве и открой первую из списка"

Messages = [
  { role: "system", content: SYSTEM_PROMPT },
  { role: "user", content: "Task: Найди на hh.ru вакансии...\n\nStart by calling get_page_state..." }
]
```

#### Шаг 1: LLM → get_page_state
```
LLM думает: "Нужно сначала посмотреть текущую страницу"
LLM вызывает: get_page_state()

→ wait_for_page_ready() (мгновенно — about:blank уже загружен)
→ extract_page_state()

Результат:
  URL: about:blank
  Title: (пустая вкладка)
  Interactive Elements: (нет)

LLM думает: "Страница пустая, нужно перейти на hh.ru"
```

#### Шаг 2: LLM → navigate
```
LLM вызывает: navigate({ url: "https://hh.ru" })

→ page.goto("https://hh.ru")
→ wait_for_page_ready():
    Уровень 1: domcontentloaded (HTML загружен за ~500мс)
    Уровень 2: DOM stability check (React рендерит контент, стабилизация за ~1.5с)
    Уровень 3: финальная пауза 300мс

Результат: { success: true, url: "https://hh.ru/", title: "Работа найдётся для каждого" }
```

#### Шаг 3: LLM → get_page_state
```
LLM вызывает: get_page_state()
Результат:
  ## Current Page
  URL: https://hh.ru/
  Title: Работа найдётся для каждого — hh.ru

  ## Page Content (summary)
  Работа найдётся для каждого | Поиск вакансий | Москва ...

  ## Interactive Elements
  [0] input[text] "Профессия, должность или компания" value=""
  [1] link "Москва"
  [2] button "Найти работу"
  [3] link "Расширенный поиск"
  [4] link "Войти"
  ...
```

#### Шаг 4: LLM → type_text
```
LLM думает: "Вижу поле поиска [0], ввожу запрос"
LLM вызывает: type_text({ ref: 0, text: "Python-разработчик" })

→ page.locator('[data-agent-ref="0"]').fill("Python-разработчик")

Результат: { success: true, description: "Typed 'Python-разработчик' into input" }
```

#### Шаг 5: LLM → click
```
LLM вызывает: click({ ref: 2 })

Security check: "Найти работу" → не матчит purchase/delete/send → not dangerous

→ page.locator('[data-agent-ref="2"]').click()
→ URL изменился → wait_for_page_ready():
    domcontentloaded: ~400мс
    DOM stability: React рендерит список вакансий ~2с
    финальная пауза: 300мс

Результат: { success: true, description: "Clicked 'Найти работу'" }
```

#### Шаг 6: LLM → get_page_state
```
LLM вызывает: get_page_state()
Результат:
  ## Current Page
  URL: https://hh.ru/search/vacancy?text=Python-разработчик&area=1
  Title: Вакансии Python-разработчик в Москве

  ## Page Content (summary)
  Найдено 2 456 вакансий | Python-разработчик | от 200 000 ₽ | ООО Яндекс ...

  ## Interactive Elements
  [0] input[text] "Профессия" value="Python-разработчик"
  [1] button "Найти"
  [2] link "Python Developer — Яндекс" → /vacancy/123456
  [3] link "Senior Python Developer — Сбер" → /vacancy/789012
  [4] link "Python Backend — Тинькофф" → /vacancy/345678
  ...
```

#### Шаг 7: LLM → click
```
LLM думает: "Первая вакансия — [2] Python Developer в Яндексе"
LLM вызывает: click({ ref: 2 })

→ click + wait_for_page_ready()

Результат: { success: true, description: "Clicked 'Python Developer — Яндекс'" }
```

#### Шаг 8: LLM → get_page_state
```
LLM вызывает: get_page_state()
Результат:
  ## Current Page
  URL: https://hh.ru/vacancy/123456
  Title: Python Developer — Яндекс

  ## Page Content (summary)
  Python Developer | Яндекс | от 250 000 до 400 000 ₽ | Москва ...

  ## Interactive Elements
  [0] button "Откликнуться"
  [1] link "Яндекс" → /employer/1740
  [2] button "Добавить в избранное"
  ...
```

#### Шаг 9: LLM → done
```
LLM думает: "Вакансия открыта. Задача выполнена."
LLM вызывает: done({
    summary: "Открыл первую вакансию 'Python Developer' в Яндексе на hh.ru. Зарплата от 250 000 до 400 000 ₽, Москва.",
    success: true
})

CLI: ✅ Открыл первую вакансию 'Python Developer' в Яндексе на hh.ru.
```

**Итого:** 9 вызовов LLM, ~8 tool calls, ~3-5 секунд на step = ~30-45 секунд на задачу.

---

## Часть 4: Структура файлов проекта

```
ai-browser-agent/
├── .github/
│   └── workflows/
│       └── ci.yml                   # CI: ruff lint + format check + pyright type check
├── src/
│   ├── __init__.py
│   ├── main.py                     # Точка входа: asyncio.run(), запуск CLI + браузер + агент
│   ├── cli.py                      # Интерактивный CLI: async readline loop, цветной вывод, команды exit/help
│   ├── browser/
│   │   ├── __init__.py
│   │   ├── controller.py           # launch_browser(), wait_for_page_ready(), обработка events
│   │   └── tools.py                # execute_tool() — реализация всех 13 browser tools
│   ├── parser/
│   │   ├── __init__.py
│   │   └── page_parser.py          # extract_page_state(), format_page_state(), JS-инъекции
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── core.py                 # run_agent() — основной LLM loop с обработкой tool_calls
│   │   ├── prompts.py              # SYSTEM_PROMPT, вспомогательные промпты
│   │   ├── tools_schema.py         # OpenAI tools definitions (JSON Schema для каждого tool)
│   │   ├── context_manager.py      # Sliding window, truncation page state
│   │   └── loop_detector.py        # Обнаружение зацикливания агента
│   ├── security/
│   │   ├── __init__.py
│   │   └── security_layer.py       # is_dangerous(), request_confirmation(), паттерны
│   └── utils/
│       ├── __init__.py
│       ├── error_recovery.py       # execute_with_retry(), классификация ошибок
│       └── logger.py               # Цветной логгер (colorama), уровни DEBUG/INFO/WARN/ERROR
├── .browser-data/                   # Persistent context Chromium (gitignored)
├── downloads/                       # Скачанные файлы (gitignored)
├── pyproject.toml                   # Зависимости, конфигурация ruff, метаданные проекта
├── uv.lock                          # Lockfile с точными версиями всего дерева зависимостей (коммитится в git)
├── .env.example                     # OPENAI_API_KEY=sk-...
├── .gitignore                       # .browser-data/, downloads/, __pycache__/, .env, .venv/
└── README.md                        # Описание, установка, запуск, демо
```

---

## Часть 5: План реализации по шагам

### Шаг 1: Инициализация проекта
**Что кодим:** `pyproject.toml`, `.env.example`, `.gitignore`, структура `src/`, `.github/workflows/ci.yml`.

**Конкретно:**
```bash
# Установка uv (если ещё нет)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Инициализация проекта
uv init ai-browser-agent && cd ai-browser-agent

# Добавление runtime-зависимостей
uv add playwright openai colorama aioconsole python-dotenv tiktoken

# Добавление dev-зависимостей (не попадут в production)
uv add --dev ruff pyright

# Установка браузера
uv run playwright install chromium

# Инициализация git + CI
git init
mkdir -p .github/workflows
```

Результат — `pyproject.toml`:
```toml
[project]
name = "ai-browser-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "playwright>=1.50.0",
    "openai>=1.60.0",
    "colorama>=0.4.6",
    "aioconsole>=0.8.0",
    "python-dotenv>=1.0.0",
    "tiktoken>=0.8.0",
]

[dependency-groups]
dev = [
    "ruff>=0.9.0",
    "pyright>=1.1.390",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "ASYNC"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"

[tool.pyright]
pythonVersion = "3.12"
typeCheckingMode = "basic"
```

`.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync --dev

      - name: Ruff lint
        run: uv run ruff check src/

      - name: Ruff format check
        run: uv run ruff format src/ --check

      - name: Type check
        run: uv run pyright src/
```

+ автоматически сгенерированный `uv.lock` с точными версиями всего дерева.

**Критерий готовности:** `uv run ruff check src/ && uv run python -c "from playwright.async_api import async_playwright; print('OK')"` — без ошибок.

**Время:** 30 мин. | **Зависимости:** нет.

---

### Шаг 2: Browser Controller — запуск браузера
**Что кодим:** `src/browser/controller.py` — `launch_browser()` с persistent context, `wait_for_page_ready()`, обработка events. `src/main.py` — запуск браузера.

**Критерий готовности:** `uv run python -m src.main` открывает видимый Chromium. Повторный запуск сохраняет cookies.

**Время:** 1 час. | **Зависимости:** Шаг 1.

---

### Шаг 3: Page Parser — извлечение состояния страницы
**Что кодим:** `src/parser/page_parser.py` — `extract_page_state()`, `format_page_state()`, JS-инъекции.

**Критерий готовности:** Открыть google.com → вызвать `extract_page_state()` → получить корректный список элементов с ref-номерами. Вывод < 5K символов.

**Время:** 2 часа. | **Зависимости:** Шаг 2.

---

### Шаг 4: Browser Tools — реализация действий
**Что кодим:** `src/browser/tools.py` — `execute_tool(name, args, page, context)` с реализацией всех 13 tools. Маппинг ref → элемент через `page.locator('[data-agent-ref="N"]')`.

**Критерий готовности:** Программный тест: navigate(google.com) → get_page_state → type_text(ref=4, text="test") → click(ref=5). Всё работает.

**Время:** 2-3 часа. | **Зависимости:** Шаг 2, Шаг 3.

---

### Шаг 5: Agent Core — LLM цикл (MVP)
**Что кодим:** `src/agent/core.py` — `run_agent()`, `src/agent/prompts.py` — system prompt, `src/agent/tools_schema.py` — OpenAI tools definitions. Простой цикл без context management и security.

**Критерий готовности:** Задача "открой google.com и найди информацию о погоде" выполняется автономно за 3-5 шагов.

**Время:** 2-3 часа. | **Зависимости:** Шаг 4.

---

### Шаг 6: CLI — интерактивный ввод
**Что кодим:** `src/cli.py` — async readline loop, цветной вывод шагов, команды `exit`/`quit`/`help`. `src/main.py` — связка.

**Критерий готовности:** Ввод задачи в терминале → агент выполняет → результат → ждёт следующую. Цветной вывод.

**Время:** 1 час. | **Зависимости:** Шаг 5.

---

### Шаг 7: Context Manager
**Что кодим:** `src/agent/context_manager.py` — sliding window, truncation, `src/agent/loop_detector.py`.

**Критерий готовности:** Длинная задача (10+ шагов) не падает. Loop detector срабатывает при повторах.

**Время:** 1-2 часа. | **Зависимости:** Шаг 5.

---

### Шаг 8: Security Layer
**Что кодим:** `src/security/security_layer.py` — regex-классификация, async запрос подтверждения.

**Критерий готовности:** Клик на "Купить" / "Отправить" → запрос подтверждения в терминале. Отклонение → LLM получает feedback.

**Время:** 1-2 часа. | **Зависимости:** Шаг 6.

---

### Шаг 9: Error Recovery
**Что кодим:** `src/utils/error_recovery.py` — retry, классификация ошибок, текстовый feedback.

**Критерий готовности:** Исчезновение элемента между get_page_state и click → агент получает ошибку и вызывает get_page_state заново.

**Время:** 1 час. | **Зависимости:** Шаг 5.

---

### Шаг 10: Screenshot + Vision fallback
**Что кодим:** screenshot tool — base64 JPEG → OpenAI vision message (`image_url` content part). Fallback-логика в agent core.

**Критерий готовности:** Агент может "увидеть" страницу через скриншот и принять решение на основе визуальной информации.

**Время:** 1-2 часа. | **Зависимости:** Шаг 5.

---

### Шаг 11: Линтинг, логгер и полировка
**Что кодим:** `src/utils/logger.py` — colorama, уровни. README.md. Прогон `ruff check --fix` + `ruff format` по всему коду. Прогон `pyright` — исправление type errors. Чистка кода.

**Критерий готовности:** `uv run ruff check src/` — 0 ошибок. `uv run ruff format src/ --check` — 0 изменений. `uv run pyright src/` — 0 errors. README с инструкцией по установке и запуску. CI проходит на GitHub.

**Время:** 1-2 часа. | **Зависимости:** Все.

---

### Шаг 12: Тестирование и запись демо
**Что кодим:** Ничего — тестируем реальные сценарии.

**Сценарии:**
1. Поиск вакансии на hh.ru
2. Поиск товара на маркетплейсе (Ozon/WB)
3. Поиск в Google → переход на Wikipedia
4. Навигация по многостраничному сайту

**Критерий готовности:** 3 из 4 сценариев успешны. Видео-демо записано.

**Время:** 3-4 часа. | **Зависимости:** Все.

---

### Суммарная оценка

| Шаг | Время | Зависимости |
|-----|-------|-------------|
| 1. Init проекта | 0.5ч | — |
| 2. Browser Controller | 1ч | 1 |
| 3. Page Parser | 2ч | 2 |
| 4. Browser Tools | 2.5ч | 2, 3 |
| 5. Agent Core (MVP) | 2.5ч | 4 |
| 6. CLI | 1ч | 5 |
| 7. Context Manager | 1.5ч | 5 |
| 8. Security Layer | 1.5ч | 6 |
| 9. Error Recovery | 1ч | 5 |
| 10. Screenshot fallback | 1.5ч | 5 |
| 11. Линтинг + логгер + полировка | 1.5ч | все |
| 12. Тестирование + демо | 3.5ч | все |
| **Итого** | **~20ч** | |

С непредвиденными проблемами: **20-25 часов**.

---

## Приложение: Неопределённости и план их разрешения

### 1. DOM extraction на SPA-сайтах
**Неопределённость:** `page.evaluate()` для сбора элементов может не захватить элементы в Shadow DOM (используется в Gmail, YouTube и т.д.).

**План:** Начинаем с обычного `querySelectorAll` (Шаг 3). Если конкретный сайт не работает — добавляем traversal через `element.shadowRoot.querySelectorAll()` рекурсивно.

### 2. DOM stability check зависает
**Неопределённость:** На сайтах с бесконечными анимациями (CSS transitions, тикеры) DOM может никогда не стабилизироваться.

**План:** Fallback timeout 5 секунд в `wait_for_page_ready()` гарантирует, что зависания не будет. Проверяем `innerHTML.length`, а не точное содержимое — мелкие анимации длину не меняют.

### 3. Маппинг ref → элемент
**Неопределённость:** Между `get_page_state` и `click` страница может измениться (динамический контент, реклама). `data-agent-ref` атрибут становится невалидным.

**План:** В `execute_tool("click")` — проверяем, существует ли `[data-agent-ref="N"]`. Если нет — возвращаем ошибку с инструкцией вызвать `get_page_state` (Error Recovery, Шаг 9).

### 4. Parallel tool calls в OpenAI
**Неопределённость:** GPT-4.1 может вернуть несколько tool_calls за один ответ (parallel tool calls включены по умолчанию). Порядок выполнения имеет значение для browser actions.

**План:** Выполняем tool calls **последовательно** в порядке получения (как в коде `run_agent`). Если нужно — отключаем через `parallel_tool_calls=False` в запросе к API. Начинаем без этого флага, добавляем если возникнут проблемы с порядком.

### 5. OpenAI vision формат для screenshots
**Неопределённость:** Формат передачи изображений в tool results OpenAI отличается от Claude. В OpenAI — через `image_url` content part с `data:image/jpeg;base64,...`.

**План:** Реализуем в Шаге 10, тестируем на конкретных примерах. Если формат в tool result не поддерживается — отправляем screenshot как отдельное user message с image.
