import asyncio

from src.browser.controller import close_browser, launch_browser, wait_for_page_ready
from src.parser.page_parser import extract_page_state


async def main() -> None:
    print("AI Browser Agent starting...")
    playwright, context, page = await launch_browser()
    try:
        await page.goto("https://www.google.com")
        await wait_for_page_ready(page)
        state = await extract_page_state(page)
        print(state.content)
    finally:
        await close_browser(context, playwright)


if __name__ == "__main__":
    asyncio.run(main())
