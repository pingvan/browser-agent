# browser-agent

`browser-agent` - это автономный браузерный агент на Playwright и OpenAI.
Он работает в видимом Chromium, сам наблюдает страницу, получает от модели следующее действие, исполняет его и продолжает цикл, пока не завершит задачу или не упрётся в guardrails.

Текущий цикл выполнения:

`observe -> decide -> act -> observe`

Проект специально устроен просто:

- один основной агент принимает runtime-решения;
- нет `LangGraph`;
- нет planner/evaluator-стадий;
- нет отдельного vision-agent;
- нет sidecar-компонента, который может завершить задачу вместо основного агента.

## Что Это За Проект

Агент умеет:

- управлять реальным браузером через Playwright;
- извлекать видимый текст страницы и интерактивные элементы;
- прикладывать к каждому шагу скриншот с помеченными элементами;
- кликать по element id или по координатам;
- вводить текст, нажимать клавиши, скроллить, переключать вкладки, ждать, переходить по URL;
- хранить устойчивые факты между шагами через `save_memory`;
- спрашивать пользователя перед чувствительными действиями;
- локально детектировать часть циклов, blocked clicks и проблемных UI-состояний.

## Как Это Работает

```text
CLI / user task
  -> Agent.run()
     -> BrowserManager.observe()
        -> page_parser.extract_page_state()
        -> annotated screenshot
     -> MessageManager
     -> OpenAI JSON-schema response
     -> ToolRegistry validation
     -> BrowserManager / state actions
     -> loop detection + security gate
     -> next observation
```

Ключевые особенности текущего runtime:

- модель возвращает не произвольный текст, а JSON по схеме `AgentOutput`;
- runtime сам валидирует имя инструмента и аргументы;
- за шаг разрешён максимум один browser action;
- state actions вроде `save_memory` можно комбинировать с этим browser action;
- старые части диалога могут сжиматься, поэтому всё важное для будущих шагов нужно сохранять через `save_memory`;
- OpenAI native tool calling сейчас не используется как execution contract: модель возвращает JSON, а инструменты исполняются локально.

## Основные Модули

- `src/agent/core.py` - главный orchestration loop
- `src/agent/message_manager.py` - сборка observation messages и compression истории
- `src/agent/tool_registry.py` - схемы действий, валидация, action signatures
- `src/agent/state.py` - состояние агента, память, fingerprints, step history
- `src/agent/prompts.py` - главный system prompt и runtime-правила
- `src/browser/controller.py` - запуск Playwright context и ожидание стабилизации страницы
- `src/browser/manager.py` - выполнение browser actions и нормализация результатов
- `src/parser/page_parser.py` - извлечение текста, интерактивных элементов, модалок и скриншотов
- `src/security/security_layer.py` - prompt-injection эвристики
- `src/security/classifier.py` и `src/security/gate.py` - классификация рискованных действий и подтверждение через CLI

## Быстрый Старт

### Требования

- Python `3.12+`
- `uv`
- `OPENAI_API_KEY`

### Установка

```bash
uv sync --dev
uv run playwright install chromium
```

### Настройка

Создайте `.env` в корне проекта:

```bash
OPENAI_API_KEY=sk-...
```

Минимальные полезные overrides:

```bash
BROWSER_AGENT_MAIN_MODEL=gpt-4o
BROWSER_AGENT_SECURITY_MODEL=gpt-4.1-nano
BROWSER_AGENT_MAX_STEPS=50
```

### Запуск

```bash
uv run python -m src.main
```

После запуска откроется видимый Chromium, а CLI начнёт принимать задачи.

Поддерживаемые встроенные команды:

- `help`
- `exit`
- `quit`

Пример:

```text
> Найди официальный сайт компании и открой страницу pricing
> Перейди в историю заказов и сохрани номера последних трёх заказов
> Найди товар, открой карточку и выпиши цену и условия доставки
```

## Поведение Браузера

- браузер запускается в visible mode;
- используется persistent profile, по умолчанию в `.browser-data`;
- locale контекста - `ru-RU`;
- загрузки сохраняются в `./downloads`;
- JavaScript dialogs автоматически принимаются в browser controller;
- на скриншоте элементы помечаются числовыми ref-метками;
- если `click(element_id)` на той же странице выглядит ненадёжно, агент может перейти на `click_coordinates(x, y, description)`;
- если клик заблокирован overlay/popup и безопасного `href` fallback нет, runtime возвращает failure вместо спама повторов.

## Доступные Действия

Browser actions:

- `navigate`
- `click`
- `click_coordinates`
- `type_text`
- `press_key`
- `scroll`
- `go_back`
- `get_tabs`
- `switch_tab`
- `wait`

State actions:

- `save_memory`
- `ask_user`
- `done`

## Безопасность

В проекте несколько защитных слоёв:

- `SecurityLayer` ищет prompt-injection паттерны в тексте страницы и labels элементов;
- `SecurityGate` отдельно классифицирует `click`, `click_coordinates` и `type_text`;
- для чувствительных действий может запрашиваться подтверждение в терминале;
- runtime дополнительно блокирует повтор одного и того же действия на той же странице и подсказывает сменить стратегию.

Это делает поведение безопаснее, но увеличивает latency и friction на checkout-like сценариях, изменениях аккаунта, отправке сообщений и других high-impact действиях.

## Конфигурация

Основные настройки лежат в `src/config/settings.py`.

Самые важные переменные:

| Variable | Default | Назначение |
| --- | --- | --- |
| `OPENAI_API_KEY` | none | Обязательный API key |
| `BROWSER_AGENT_MAIN_MODEL` | `gpt-4o` | Основная модель принятия решений |
| `BROWSER_AGENT_SECURITY_MODEL` | `gpt-4.1-nano` | Модель security classifier |
| `BROWSER_AGENT_SECURITY_USE_SCREENSHOT` | `false` | Передавать ли screenshot в security classification |
| `BROWSER_AGENT_BROWSER_DATA_DIR` | `.browser-data` | Папка persistent browser profile |
| `BROWSER_AGENT_VIEWPORT_WIDTH` | `1280` | Ширина viewport |
| `BROWSER_AGENT_VIEWPORT_HEIGHT` | `900` | Высота viewport |
| `BROWSER_AGENT_NAVIGATION_TIMEOUT_MS` | `30000` | Таймаут навигации |
| `BROWSER_AGENT_MAX_STEPS` | `50` | Максимум шагов на run |
| `BROWSER_AGENT_MAX_RETRIES_PER_STEP` | `3` | Порог repeated failures |
| `BROWSER_AGENT_MAX_STUCK_STEPS` | `4` | Порог stuck behavior |
| `BROWSER_AGENT_SCREENSHOT_QUALITY` | `75` | Качество JPEG screenshot |

В `src/config/settings.py` есть и другие переменные.
Часть старых knobs может быть исторической или неактивной, поэтому source of truth - код, а не только имя переменной.

## Разработка

Базовые команды:

```bash
uv run python -m src.main
uv run pytest -q
uv run ruff check .
uv run pyright
```

## Отладка

На каждом model step runtime пишет debug artifacts в `debug_dumps/`:

- `step_XXX_messages.json` - snapshot сообщений без реального base64 screenshot payload
- `step_XXX_summary.txt` - читаемая выжимка по шагу и распарсенный `AgentOutput`

Самые полезные лог-блоки при разборе поведения:

- `OBSERVE`
- `AGENT REASONING`
- `EXECUTE`
- browser logs из `BrowserManager`
- security verdict logs из `SecurityGate`

## Структура Репозитория

```text
.
├── README.md
├── AGENTS.md
├── CLAUDE.md
├── src
│   ├── agent
│   ├── browser
│   ├── config
│   ├── parser
│   ├── security
│   ├── cli.py
│   └── main.py
└── tests
```