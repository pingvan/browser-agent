import asyncio
import base64
from pathlib import Path

from src.browser import tools
from src.browser.controller import close_browser, launch_browser
from src.browser.tools import execute_tool


async def main() -> None:
    print("AI Browser Agent starting...")
    playwright, context, page = await launch_browser()
    try:
        # navigate
        result = await execute_tool("navigate", {"url": "https://www.google.com"}, page, context)
        print("navigate:", result)

        # get_page_state — also populates tools._last_page_state
        state_content = await execute_tool("get_page_state", {}, page, context)
        print(state_content)

        # find search input dynamically from the parsed elements
        search_ref: int | None = None
        if tools._last_page_state is not None:
            for el in tools._last_page_state.elements:
                if el.role == "combobox" or el.input_type in ("search", "text"):
                    search_ref = el.ref
                    break

        if search_ref is None:
            print("Could not find search input in page state")
        else:
            type_result = await execute_tool(
                "type_text",
                {"ref": search_ref, "text": "Playwright Python", "press_enter": False},
                page,
                context,
            )
            print("type_text:", type_result)

        # screenshot
        shot_result = await execute_tool("screenshot", {}, page, context)
        if isinstance(shot_result, dict) and "base64_image" in shot_result:
            img_data = base64.b64decode(shot_result["base64_image"])
            await asyncio.to_thread(Path("screenshot_test.jpg").write_bytes, img_data)
            print(f"Screenshot saved: {len(img_data)} bytes → screenshot_test.jpg")
        else:
            print("screenshot result:", shot_result)

    finally:
        await close_browser(context, playwright)


if __name__ == "__main__":
    asyncio.run(main())
