import asyncio
import signal

import aioconsole
from colorama import Fore, Style
from playwright.async_api import BrowserContext, Page

from src.agent.core import run_agent

_HELP = """Available commands:
  <task>   — describe what the agent should do (in any language)
  help     — show this help message
  exit     — close the browser and quit
  quit     — alias for exit"""

_R = Style.RESET_ALL


async def run_cli(page: Page, context: BrowserContext) -> None:
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    agent_task: asyncio.Task[str] | None = None

    def _on_sigint() -> None:
        if agent_task is not None and not agent_task.done():
            agent_task.cancel()
        elif main_task is not None and not main_task.done():
            main_task.cancel()

    loop.add_signal_handler(signal.SIGINT, _on_sigint)

    try:
        while True:
            await aioconsole.aprint(Fore.CYAN + f"📍 Current page: {page.url}" + _R)
            try:
                user_input = (await aioconsole.ainput("\n> ")).strip()
            except (KeyboardInterrupt, asyncio.CancelledError, EOFError):
                print()
                return

            if user_input in ("exit", "quit"):
                return

            if user_input == "help":
                await aioconsole.aprint(Fore.CYAN + _HELP + _R)
                continue

            if not user_input:
                continue

            agent_task = asyncio.create_task(run_agent(task=user_input, page=page, context=context))

            try:
                result = await agent_task
                await aioconsole.aprint(Fore.GREEN + result + _R)
            except asyncio.CancelledError:
                print(Fore.YELLOW + "\n⚠️  Task interrupted" + _R)
            except Exception as e:
                await aioconsole.aprint(Fore.RED + f"Error: {e}" + _R)
            finally:
                agent_task = None

            await aioconsole.aprint("—" * 40)
    finally:
        loop.remove_signal_handler(signal.SIGINT)
