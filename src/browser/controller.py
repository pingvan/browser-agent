import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Download, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from src.config.settings import BROWSER_DATA_DIR, VIEWPORT_HEIGHT, VIEWPORT_WIDTH


async def launch_browser() -> tuple[Playwright, BrowserContext, Page]:
    await asyncio.to_thread(Path("./downloads").mkdir, exist_ok=True)

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        BROWSER_DATA_DIR,
        headless=False,
        handle_sigint=False,
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        locale="ru-RU",
        args=["--disable-blink-features=AutomationControlled"],
    )

    page = context.pages[0] if context.pages else await context.new_page()

    page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))

    def _on_download(download: Download) -> None:
        safe_name = Path(download.suggested_filename).name
        asyncio.create_task(download.save_as(Path("./downloads") / safe_name))

    context.on("download", _on_download)  # type: ignore[call-overload]

    return playwright, context, page


async def wait_for_page_ready(page: Page, load_timeout_ms: int = 10000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=load_timeout_ms)
    except PlaywrightError:
        pass


async def close_browser(context: BrowserContext, playwright: Playwright) -> None:
    try:
        await context.close()
    except Exception:
        pass
    try:
        await playwright.stop()
    except Exception:
        pass
