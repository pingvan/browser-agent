import asyncio

from dotenv import load_dotenv

from src.agent.core import run_agent
from src.browser.controller import close_browser, launch_browser


async def main() -> None:
    load_dotenv()
    print("AI Browser Agent starting...")
    playwright, context, page = await launch_browser()
    try:
        await run_agent(
            "открой vk.com и отпишись от всех пбаликов за исключением первых 7ми", page, context
        )
        await asyncio.sleep(10)
    finally:
        await close_browser(context, playwright)


if __name__ == "__main__":
    asyncio.run(main())
