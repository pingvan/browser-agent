import json
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from playwright.async_api import BrowserContext, Page

from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools_schema import TOOLS
from src.browser.tools import execute_tool
from src.utils.logger import logger

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

        try:
            response = await client.chat.completions.create(
                model="gpt-4.1",
                messages=cast(list[ChatCompletionMessageParam], messages),
                tools=cast(Any, TOOLS),
                tool_choice="auto",
                max_tokens=4096,
            )
        except Exception as e:
            return f"API error: {e}"
        message = response.choices[0].message
        messages.append({k: v for k, v in message.model_dump().items() if v is not None})

        if not message.tool_calls:
            logger.info(message.content or "")
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "If you have completed the task, call the `done` tool with a concise summary "
                        "of the result. Otherwise, continue using the available tools to make progress "
                        "toward completing the task."
                    ),
                }
            )
            continue

        for tool_call in cast(list[ChatCompletionMessageToolCall], message.tool_calls):
            fn_name = tool_call.function.name
            try:
                fn_args: dict[str, Any] = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                logger.error(f"JSONDecodeError for tool '{fn_name}': {e}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            {
                                "error": "InvalidToolArguments",
                                "message": f"Failed to parse arguments for tool '{fn_name}': {e}",
                                "raw_arguments": tool_call.function.arguments,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                continue
            logger.info(f"Step {step}: {fn_name}({fn_args})")

            result, page = await execute_tool(fn_name, fn_args, page, context)

            tool_content = (
                "Screenshot captured."
                if "base64_image" in result
                else json.dumps(result, ensure_ascii=False)
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_content,
                }
            )

            if fn_name == "done":
                summary = str(result.get("summary", ""))
                logger.info(summary)
                return summary

            if "base64_image" in result:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{result['base64_image']}",
                                    "detail": "low",
                                },
                            }
                        ],
                    }
                )

    return "Max steps reached"
