import json
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from playwright.async_api import BrowserContext, Page

from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools_schema import TOOLS
from src.browser.tools import execute_tool

MAX_STEPS = 50


async def run_agent(task: str, page: Page, context: BrowserContext) -> str:
    client = AsyncOpenAI()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Task: {task}\n\nStart by calling get_page_state to see the current page.",
        },
    ]

    step = 0
    while step < MAX_STEPS:
        step += 1

        response = await client.chat.completions.create(
            model="gpt-4.1",
            messages=cast(list[ChatCompletionMessageParam], messages),
            tools=cast(Any, TOOLS),
            tool_choice="auto",
            max_tokens=4096,
        )
        message = response.choices[0].message
        messages.append(message.model_dump())

        if not message.tool_calls:
            text = message.content or ""
            print(text)
            return text

        for tool_call in cast(list[ChatCompletionMessageToolCall], message.tool_calls):
            fn_name = tool_call.function.name
            fn_args: dict[str, Any] = json.loads(tool_call.function.arguments)
            print(f"🔄 Step {step}: {fn_name}({fn_args})")

            result, page = await execute_tool(fn_name, fn_args, page, context)

            content: str | list[dict[str, Any]]
            if "base64_image" in result:
                content = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{result['base64_image']}",
                            "detail": "low",
                        },
                    }
                ]
            else:
                content = json.dumps(result, ensure_ascii=False)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": content,
                }
            )

            if fn_name == "done":
                summary = str(result.get("summary", ""))
                print(summary)
                return summary

    return "Max steps reached"
