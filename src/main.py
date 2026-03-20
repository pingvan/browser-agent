import asyncio
import base64
from pathlib import Path

from src.browser.controller import close_browser, launch_browser
from src.browser.tools import execute_tool


async def main() -> None:
    print("AI Browser Agent starting...")
    playwright, context, page = await launch_browser()
    try:
        # navigate
        result = await execute_tool("navigate", {"url": "https://www.google.com"}, page, context)
        print("navigate:", result)

        # get_page_state
        state_content = await execute_tool("get_page_state", {}, page, context)
        print(state_content)

        # search combobox is ref=15 on google.com ("Найти")
        type_result = await execute_tool(
            "type_text",
            {"ref": 15, "text": "Playwright Python", "press_enter": False},
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
