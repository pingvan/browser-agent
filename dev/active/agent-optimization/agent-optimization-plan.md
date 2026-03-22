# Agent Optimization Plan

Last Updated: 2026-03-22

---

## Executive Summary

Каждый шаг агента занимает 5-10+ секунд. Основные причины задержки — трёхуровневое ожидание загрузки (`wait_for_page_ready`) и автоматическое извлечение page state + screenshot после каждого действия.

Стратегия: сначала убрать лишние задержки и заставить агента работать быстро, оптимизацию токенов/промпта делать потом при необходимости.

---

## Current State

### wait_for_page_ready — 3 уровня, 1.5-5 секунд на каждый вызов

| Уровень | Что делает | Overhead |
|---------|-----------|----------|
| Level 1: domcontentloaded | Ждёт парсинга HTML | ~0.5s — **оставляем** |
| Level 2: DOM stability | Polling fingerprint каждые 150ms, timeout 3s | 0.3-3s — **убираем** |
| Level 3: Spinner detection | Ждёт исчезновения `.spinner`, `.loader`, etc., timeout 1.5s | 0-1.5s — **убираем** |

Вызывается 6 раз: navigate, click, go_back, type_text+Enter, select_option, press_key (navigation keys).

### Auto page state extraction — 150-400ms после каждого page-changing action

`execute_tool()` (tools.py:369-377) автоматически вызывает `extract_page_state_with_screenshot()` для всех инструментов из `_PAGE_CHANGING_TOOLS` (navigate, click, go_back, select_option, type_text, press_key, switch_tab). Это DOM extraction + JPEG screenshot на каждый шаг, даже если агенту не нужен результат.

---

## Changes

### 1. Упростить wait_for_page_ready

В `src/browser/controller.py` оставить только:
```python
async def wait_for_page_ready(page: Page, load_timeout_ms: int = 10000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=load_timeout_ms)
    except PlaywrightError:
        pass
```

Убрать Level 2 (DOM stability) и Level 3 (spinner detection). Агент имеет `wait` tool и может подождать сам, если страница не дозагрузилась.

### 2. Lazy page state

Убрать автоматический extract+screenshot из `execute_tool()` для page-changing tools. Оставить автоматический page state только для `navigate` (агент сразу видит куда попал). Для остальных (click, type_text, etc.) — агент вызывает `get_page_state` явно.

### 3. Промпт

Добавить в промпт указание: после действий вызывай `get_page_state`, чтобы увидеть результат.

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Агент видит skeleton/incomplete page без DOM stability wait | Агент вызывает `wait` + `get_page_state` |
| Агент "слепнет" без auto page state после click/type_text | Промпт инструктирует вызывать `get_page_state` |
| Больше шагов (явный get_page_state = +1 action) | Каждый шаг быстрее → общее время то же или меньше |
