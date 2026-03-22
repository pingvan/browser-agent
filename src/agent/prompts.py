from __future__ import annotations

from src.agent.state import AgentState, render_memory, render_plan, render_recent_history
from src.config.settings import MAX_STEPS

SYSTEM_PROMPT = """
Ты — автономный AI-агент, управляющий веб-браузером.

Ты работаешь пошагово:
plan -> observe -> think -> act -> check -> repeat.

Правила:
- Следуй текущему плану, но адаптируйся к странице.
- Если находишь важный факт, сначала сохрани его через save_memory.
- Если шаг плана выполнен, отметь его через complete_plan_step.
- Если подход застрял, запроси replan.
- Если не хватает информации от пользователя, используй ask_user.
- Никогда не подтверждай оплату, отправку, удаление или публикацию без ask_user.
- Верни строго JSON-объект:
  {
    "reasoning": "краткое объяснение",
    "actions": [
      {"action": "...", "...": "..."}
    ]
  }

Доступные действия:
- click(element_id)
- type(element_id, text, press_enter?)
- press_key(key)
- navigate(url)
- scroll(direction, amount?)
- go_back()
- wait(seconds?)
- save_memory(key, value)
- complete_plan_step(step_id, result)
- replan(reason)
- ask_user(question)
- done(summary)

Ограничение:
- Разрешён максимум один браузерный action за ответ.
- Можно вернуть несколько save_memory / complete_plan_step перед браузерным action.
""".strip()

PLANNER_SYSTEM_PROMPT = """
Ты — планировщик задач для браузерного агента.
Разбей задачу на 3-10 конкретных браузерных шагов.

Правила:
- Каждый шаг должен быть выполним через браузер.
- Если данные с одной страницы нужны дальше, явно укажи "сохранить в память".
- Не хардкодь селекторы и точные названия кнопок.
- Если информации не хватает, добавь шаг "спросить пользователя".
- Последний шаг должен завершать задачу отчётом пользователю или остановкой перед опасным действием.

Ответь строго JSON:
{
  "reasoning": "почему такой план",
  "steps": ["...", "..."]
}
""".strip()

PAGE_ANALYZER_PROMPT = """
Кратко опиши, что происходит на странице:
- какой это тип страницы,
- что на ней главное,
- какие 2-4 действия выглядят наиболее релевантными для выполнения задачи.

Пиши кратко, не более 120 слов.
""".strip()

FIND_ELEMENT_PROMPT = """
Тебе задан вопрос о странице и список интерактивных элементов.
Ответь кратко: какой элемент лучше всего подходит и почему.
Если подходящего элемента нет, так и скажи.
""".strip()


def build_step_prompt(state: AgentState) -> str:
    task = state.get("task", "")
    plan = render_plan(state.get("plan", []), state.get("current_plan_step", -1))
    memory = render_memory(state.get("memory", []))
    recent_history = render_recent_history(state.get("action_history", []))
    history_summary = state.get("history_summary", "")
    page_summary = state.get("page_summary", "")
    current_url = state.get("current_url", "")
    page_title = state.get("page_title", "")
    last_error = state.get("last_error", "")
    user_response = state.get("user_response")

    elements_lines = []
    for element in state.get("interactive_elements", []):
        label = (
            element.get("aria_label")
            or element.get("text")
            or element.get("placeholder")
            or "(no label)"
        )
        role = element.get("role") or element.get("tag") or "element"
        extra = []
        if element.get("href"):
            extra.append(f'href="{element.get("href", "")}"')
        if element.get("value"):
            extra.append(f'value="{element.get("value", "")}"')
        if element.get("disabled"):
            extra.append("disabled")
        suffix = f" | {' | '.join(extra)}" if extra else ""
        elements_lines.append(f'[{element.get("index", element.get("ref", "?"))}] {role} | "{label}"{suffix}')
    elements_text = "\n".join(elements_lines) if elements_lines else "(no interactive elements)"

    sections = [
        "## Задача",
        task,
        "",
        "## План",
        plan,
        "",
        "## Рабочая память",
        memory,
        "",
        "## Последние действия",
        recent_history,
    ]

    if history_summary:
        sections.extend(["", "## Сводка старой истории", history_summary])

    sections.extend(
        [
            "",
            "## Текущая страница",
            f"URL: {current_url}",
            f"Title: {page_title}",
            page_summary or "(page summary unavailable)",
        ]
    )

    if last_error:
        sections.extend(["", "## Последняя ошибка", last_error])

    if user_response:
        sections.extend(["", "## Последний ответ пользователя", user_response])

    sections.extend(
        [
            "",
            "## Интерактивные элементы",
            elements_text,
            "",
            f"## Шаг {state.get('step_count', 0) + 1}/{MAX_STEPS}",
            "Выбери следующий шаг и ответь JSON-объектом.",
        ]
    )
    return "\n".join(sections)
