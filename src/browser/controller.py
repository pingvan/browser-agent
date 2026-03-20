import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Download, Page, Playwright, async_playwright


async def launch_browser() -> tuple[Playwright, BrowserContext, Page]:
    await asyncio.to_thread(Path("./downloads").mkdir, exist_ok=True)

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        ".browser-data",
        headless=False,
        viewport={"width": 1280, "height": 900},
        locale="ru-RU",
        args=["--disable-blink-features=AutomationControlled"],
    )

    page = context.pages[0] if context.pages else await context.new_page()

    page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))

    async def _on_download(download: Download) -> None:
        await download.save_as(Path("./downloads") / download.suggested_filename)

    context.on("download", _on_download)  # type: ignore[call-overload]

    return playwright, context, page


async def wait_for_page_ready(page: Page, timeout: int = 10000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass

    try:
        deadline = asyncio.get_running_loop().time() + 5
        prev_length = -1
        stable_count = 0
        while asyncio.get_running_loop().time() < deadline:
            length: int = await page.evaluate("document.body ? document.body.innerHTML.length : 0")
            if length == prev_length:
                stable_count += 1
                if stable_count >= 3:
                    break
            else:
                stable_count = 0
                prev_length = length
            await asyncio.sleep(0.2)
    except Exception:
        pass

    try:
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def close_browser(context: BrowserContext, playwright: Playwright) -> None:
    await context.close()
    await playwright.stop()
