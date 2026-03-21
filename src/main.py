import asyncio

from colorama import Fore
from dotenv import load_dotenv

from src.browser.controller import close_browser, launch_browser
from src.cli import run_cli


async def main() -> None:
    load_dotenv()
    print(Fore.CYAN + "🤖 AI Browser Agent\033[0m")
    print(Fore.CYAN + "Type a task or 'exit' to quit\033[0m")
    playwright, context, page = await launch_browser()
    try:
        await run_cli(page, context)
    finally:
        await close_browser(context, playwright)


if __name__ == "__main__":
    asyncio.run(main())
