import asyncio

from aioconsole import ainput

from src.browser.controller import close_browser, launch_browser


async def main() -> None:
    print("AI Browser Agent starting...")
    playwright, context, page = await launch_browser()
    try:
        print(f"Browser opened. Current URL: {page.url}")
        await ainput("Press Enter to close...")
    finally:
        await close_browser(context, playwright)


if __name__ == "__main__":
    asyncio.run(main())
