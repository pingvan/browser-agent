from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Any

from playwright.async_api import BrowserContext, Page

from src.agent.state import ElementSnapshot
from src.browser.controller import wait_for_page_ready
from src.config.settings import ACTION_DELAY_MS, NAVIGATION_TIMEOUT_MS, SCREENSHOT_QUALITY
from src.parser.page_parser import (
    InteractiveElement,
    PageState,
    extract_page_state,
    take_screenshot,
)
from src.utils.logger import logger

_NAVIGATION_KEYS: frozenset[str] = frozenset(
    {"Enter", "Return", "F5", "Alt+ArrowLeft", "Alt+ArrowRight"}
)

_ANNOTATION_INJECT_SCRIPT = """
(boxes) => {
    const existing = document.getElementById('__agent-annotation-root__');
    if (existing) existing.remove();

    const root = document.createElement('div');
    root.id = '__agent-annotation-root__';
    Object.assign(root.style, {
        position: 'fixed',
        inset: '0px',
        pointerEvents: 'none',
        zIndex: '2147483647',
    });

    for (const box of boxes) {
        const frame = document.createElement('div');
        Object.assign(frame.style, {
            position: 'fixed',
            left: `${box.x}px`,
            top: `${box.y}px`,
            width: `${Math.max(box.width, 8)}px`,
            height: `${Math.max(box.height, 8)}px`,
            border: '2px solid #ff3b30',
            borderRadius: '6px',
            boxSizing: 'border-box',
            background: 'rgba(255, 59, 48, 0.08)',
        });

        const badge = document.createElement('div');
        badge.textContent = `[${box.ref}]`;
        Object.assign(badge.style, {
            position: 'absolute',
            left: '-2px',
            top: '-24px',
            background: '#ff3b30',
            color: 'white',
            fontSize: '12px',
            lineHeight: '16px',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontWeight: '700',
            padding: '2px 6px',
            borderRadius: '999px',
            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.2)',
        });

        frame.appendChild(badge);
        root.appendChild(frame);
    }

    (document.documentElement || document.body).appendChild(root);
}
"""

_ANNOTATION_REMOVE_SCRIPT = """
() => {
    document.getElementById('__agent-annotation-root__')?.remove();
}
"""


@dataclass
class Observation:
    page_state: PageState
    screenshot_b64: str
    elements: list[ElementSnapshot]


class BrowserManager:
    def __init__(self, page: Page, context: BrowserContext) -> None:
        self.page = page
        self.context = context

    async def observe(self) -> Observation:
        logger.debug(f"Browser.observe: url={self.page.url}")
        page_state = await extract_page_state(self.page)
        screenshot_b64 = await self.take_annotated_screenshot(page_state.elements)
        logger.debug(
            f"Browser.observe result: title={page_state.title[:100]!r}, elements={len(page_state.elements)}, "
            f"screenshot={'yes' if screenshot_b64 else 'no'}"
        )
        return Observation(
            page_state=page_state,
            screenshot_b64=screenshot_b64,
            elements=[self._to_snapshot(element) for element in page_state.elements],
        )

    async def take_annotated_screenshot(self, elements: list[InteractiveElement]) -> str:
        boxes = []
        for element in elements:
            if element.bbox is None:
                continue
            boxes.append(
                {
                    "ref": element.ref,
                    "x": element.bbox.x,
                    "y": element.bbox.y,
                    "width": element.bbox.width,
                    "height": element.bbox.height,
                }
            )

        if boxes:
            try:
                await self.page.evaluate(_ANNOTATION_INJECT_SCRIPT, boxes)
            except Exception as exc:
                logger.debug(f"Browser.take_annotated_screenshot: annotation inject failed: {exc}")
                boxes = []

        try:
            return await take_screenshot(self.page, quality=SCREENSHOT_QUALITY)
        finally:
            if boxes:
                try:
                    await self.page.evaluate(_ANNOTATION_REMOVE_SCRIPT)
                except Exception as exc:
                    logger.debug(f"Browser.take_annotated_screenshot: annotation cleanup failed: {exc}")
                    pass

    async def navigate(self, url: str) -> dict[str, Any]:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported URL scheme: {url!r}")
        logger.info(f"Browser.navigate: {url}")
        await self.page.goto(url, timeout=NAVIGATION_TIMEOUT_MS)
        await self._wait_for_stable()
        return {"success": True, "description": f"Opened {self.page.url}"}

    async def go_back(self) -> dict[str, Any]:
        logger.info("Browser.go_back")
        result = await self.page.go_back(timeout=NAVIGATION_TIMEOUT_MS)
        if result is None:
            raise RuntimeError("No previous page in browser history")
        await self._wait_for_stable()
        return {"success": True, "description": f"Returned to {self.page.url}"}

    async def click(self, element_id: int, elements: list[ElementSnapshot]) -> dict[str, Any]:
        x, y = self._center_for_element(element_id, elements)
        logger.info(f"Browser.click: element_id={element_id}, x={x}, y={y}")
        try:
            await self.page.mouse.click(x, y)
        except Exception as exc:
            logger.debug(f"Browser.click: coordinate click failed, falling back to locator: {exc}")
            locator = self.page.locator(f'[data-agent-ref="{element_id}"]')
            await locator.click(timeout=5000)
        await self._wait_for_stable()
        return {"success": True, "description": f"Clicked element [{element_id}]"}

    async def type_text(
        self,
        element_id: int,
        text: str,
        elements: list[ElementSnapshot],
        *,
        press_enter: bool = False,
    ) -> dict[str, Any]:
        x, y = self._center_for_element(element_id, elements)
        logger.info(
            f"Browser.type_text: element_id={element_id}, chars={len(text)}, press_enter={press_enter}"
        )
        await self.page.mouse.click(x, y, click_count=3)
        modifier = "Meta+A" if sys.platform == "darwin" else "Control+A"
        await self.page.keyboard.press(modifier)
        await self.page.keyboard.press("Backspace")
        await self.page.keyboard.type(text, delay=30)
        if press_enter:
            await self.page.keyboard.press("Enter")
            await self._wait_for_stable()
        return {
            "success": True,
            "description": f"Typed {len(text)} characters into [{element_id}]",
        }

    async def press_key(self, key: str) -> dict[str, Any]:
        logger.info(f"Browser.press_key: key={key}")
        await self.page.keyboard.press(key)
        if key in _NAVIGATION_KEYS:
            await self._wait_for_stable()
        return {"success": True, "description": f"Pressed key {key}"}

    async def scroll(self, direction: str, amount: int = 500) -> dict[str, Any]:
        if direction not in {"up", "down"}:
            raise ValueError(f"Invalid scroll direction: {direction!r}")
        logger.info(f"Browser.scroll: direction={direction}, amount={amount}")
        delta = abs(amount)
        if direction == "up":
            delta = -delta
        await self.page.evaluate("(delta) => window.scrollBy(0, delta)", delta)
        await self.page.wait_for_timeout(200)
        scroll_y = await self.page.evaluate("window.scrollY")
        return {"success": True, "description": f"Scrolled {direction}", "scroll_y": scroll_y}

    async def wait(self, seconds: float = 2.0) -> dict[str, Any]:
        seconds = min(max(seconds, 0.0), 10.0)
        logger.info(f"Browser.wait: seconds={seconds:.1f}")
        await self.page.wait_for_timeout(int(seconds * 1000))
        return {"success": True, "description": f"Waited {seconds:.1f}s"}

    async def get_tabs(self) -> dict[str, Any]:
        logger.debug(f"Browser.get_tabs: total_tabs={len(self.context.pages)}")
        tabs = []
        for index, page in enumerate(self.context.pages):
            tabs.append(
                {
                    "index": index,
                    "url": page.url,
                    "title": await page.title(),
                    "active": page is self.page,
                }
            )
        return {"success": True, "tabs": tabs}

    async def switch_tab(self, index: int) -> dict[str, Any]:
        pages = self.context.pages
        if index < 0 or index >= len(pages):
            raise ValueError(f"Tab index {index} out of range")
        logger.info(f"Browser.switch_tab: index={index}")
        self.page = pages[index]
        await self.page.bring_to_front()
        await self._wait_for_stable()
        return {"success": True, "description": f"Switched to tab #{index}", "url": self.page.url}

    async def _wait_for_stable(self) -> None:
        logger.debug(
            f"Browser._wait_for_stable: timeout_ms={NAVIGATION_TIMEOUT_MS}, action_delay_ms={ACTION_DELAY_MS}"
        )
        await wait_for_page_ready(self.page, load_timeout_ms=NAVIGATION_TIMEOUT_MS)
        await self.page.wait_for_timeout(ACTION_DELAY_MS)

    def _center_for_element(self, element_id: int, elements: list[ElementSnapshot]) -> tuple[int, int]:
        for element in elements:
            ref = element.get("index", element.get("ref"))
            if ref != element_id:
                continue
            bbox = element.get("bbox")
            if not bbox:
                break
            return (
                int(bbox["x"] + bbox["width"] / 2),
                int(bbox["y"] + bbox["height"] / 2),
            )
        raise ValueError(f"Element [{element_id}] not found in current observation")

    def _to_snapshot(self, element: InteractiveElement) -> ElementSnapshot:
        snapshot = ElementSnapshot(**asdict(element))
        snapshot["index"] = element.ref
        return snapshot
