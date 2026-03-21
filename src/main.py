import asyncio

import time

from dotenv import load_dotenv

from src.agent.core import run_agent
from src.browser.controller import close_browser, launch_browser


async def main() -> None:
    load_dotenv()
    print("AI Browser Agent starting...")
    playwright, context, page = await launch_browser()
    try:
        summary = await run_agent("открой google.com и найди погоду в Москве", page, context)
        print(f"Result: {summary}")
        time.sleep(10)
    finally:
        await close_browser(context, playwright)


if __name__ == "__main__":
    asyncio.run(main())
