import asyncio

from colorama import Fore, Style
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()
    from src.browser.controller import close_browser, launch_browser
    from src.cli import run_cli

    print(Fore.CYAN + "🤖 AI Browser Agent\033[0m")
    print(Fore.CYAN + "Type a task or 'exit' to quit\033[0m")
    playwright, context, page = await launch_browser()
    try:
        await run_cli(page, context)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(Fore.YELLOW + "\nGoodbye!" + Style.RESET_ALL)
    finally:
        task = asyncio.current_task()
        if task is not None:
            while task.cancelling() > 0:
                task.uncancel()
        await close_browser(context, playwright)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
