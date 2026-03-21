import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Download, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError


async def launch_browser() -> tuple[Playwright, BrowserContext, Page]:
    await asyncio.to_thread(Path("./downloads").mkdir, exist_ok=True)

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        ".browser-data",
        headless=False,
        handle_sigint=False,
        viewport={"width": 1280, "height": 900},
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


async def wait_for_page_ready(page: Page, timeout: int = 10000) -> None:
    """
    Трёхуровневая стратегия ожидания загрузки:
    
    1. domcontentloaded — HTML распарсен
    2. Умная стабилизация — ждём ЗНАЧИМЫХ изменений DOM
       (игнорируем мелкие анимации)
    3. Опциональное ожидание исчезновения спиннеров
    """

    # ─── Уровень 1: базовая загрузка HTML ───
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except PlaywrightError:
        pass

    # ─── Уровень 2: умная стабилизация ───
    # Вместо innerHTML.length (который реагирует на каждую мелочь)
    # считаем количество элементов + длину текста.
    # Это игнорирует CSS-анимации, таймеры и мелкие DOM-изменения.
    try:
        await page.evaluate("""
        () => new Promise((resolve) => {
            // Функция "отпечатка" страницы — меняется только при значимых изменениях
            function getFingerprint() {
                const body = document.body;
                if (!body) return '0:0';
                // Количество элементов + длина видимого текста
                // Не реагирует на анимации, таймеры, мелкие обновления
                const elementCount = body.querySelectorAll('*').length;
                const textLength = body.innerText.length;
                // Округляем текст до сотен — мелкие изменения (таймер "до конца акции 14:59")
                // не будут считаться значимыми
                return elementCount + ':' + Math.round(textLength / 100);
            }
            
            let prev = '';
            let stableCount = 0;
            
            const interval = setInterval(() => {
                const current = getFingerprint();
                if (current === prev) {
                    stableCount++;
                    if (stableCount >= 2) {           // 2 проверки вместо 3
                        clearInterval(interval);
                        resolve('stable');
                    }
                } else {
                    stableCount = 0;
                    prev = current;
                }
            }, 150);                                   // 150мс вместо 200мс
            
            setTimeout(() => {
                clearInterval(interval);
                resolve('timeout');
            }, 3000);                                  // 3с вместо 5с
        })
        """)
    except PlaywrightError:
        pass

    # ─── Уровень 3: ждём исчезновения спиннеров (если они есть) ───
    # Не ждём фиксированные 300мс — ждём КОНКРЕТНЫЙ сигнал
    try:
        # Даём 1.5с на исчезновение спиннеров. Если их нет — выходим мгновенно.
        await page.wait_for_function(
            """
            () => {
                const loaders = document.querySelectorAll(
                    '.spinner, .skeleton, .loading, ' +
                    '[class*="loader"], [class*="shimmer"], [class*="preloader"], ' +
                    '[class*="skeleton"], [aria-busy="true"]'
                );
                // Проверяем, есть ли ВИДИМЫЕ спиннеры
                for (const el of loaders) {
                    const style = window.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden' 
                        && el.offsetHeight > 0) {
                        return false;  // ещё есть видимый спиннер — ждём
                    }
                }
                return true;  // спиннеров нет — готово
            }
            """,
            timeout=1500,
        )
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
