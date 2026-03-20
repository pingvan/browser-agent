import asyncio

from src.browser.controller import close_browser, launch_browser


async def main() -> None:
    print("AI Browser Agent starting...")
    playwright, context, _ = await launch_browser()
    try:
        print("Browser launched. CLI not yet implemented.")
        # TODO: run_cli(page, context) once cli.py is implemented
    finally:
        await close_browser(context, playwright)


if __name__ == "__main__":
    asyncio.run(main())
