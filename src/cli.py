import asyncio

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
    try:
        while True:
            await aioconsole.aprint(Fore.CYAN + f"📍 Current page: {page.url}" + _R)
            user_input = (await aioconsole.ainput("\n> ")).strip()

            if user_input in ("exit", "quit"):
                break

            if user_input == "help":
                await aioconsole.aprint(Fore.CYAN + _HELP + _R)
                continue

            if not user_input:
                continue

            try:
                result = await run_agent(task=user_input, page=page, context=context)
                await aioconsole.aprint(Fore.GREEN + result + _R)
            except Exception as e:
                await aioconsole.aprint(Fore.RED + f"Error: {e}" + _R)

            await aioconsole.aprint("—" * 40)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print()
        raise
