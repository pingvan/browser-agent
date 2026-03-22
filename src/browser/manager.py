from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Any

from playwright.async_api import BrowserContext, Page

from src.agent.state import ElementSnapshot, normalize_url_for_fingerprint
from src.browser.controller import wait_for_page_ready
from src.config.settings import NAVIGATION_TIMEOUT_MS, SCREENSHOT_QUALITY
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
    tab_count: int


class BrowserManager:
    def __init__(self, page: Page, context: BrowserContext) -> None:
        self.page = page
        self.context = context

    async def observe(self, *, capture_screenshot: bool = True) -> Observation:
        logger.debug(f"Browser.observe: url={self.page.url}")
        page_state = await extract_page_state(self.page)
        screenshot_b64 = (
            await self.take_annotated_screenshot(page_state.elements) if capture_screenshot else ""
        )
        logger.debug(
            f"Browser.observe result: title={page_state.title[:100]!r}, elements={len(page_state.elements)}, "
            f"screenshot={'yes' if screenshot_b64 else 'no'}"
        )
        return Observation(
            page_state=page_state,
            screenshot_b64=screenshot_b64,
            elements=[self._to_snapshot(element) for element in page_state.elements],
            tab_count=len(self.context.pages),
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
        url_before = self.page.url
        logger.info(f"Browser.navigate: {url}")
        await self.page.goto(url, timeout=NAVIGATION_TIMEOUT_MS)
        await self._wait_for_stable(wait_for_dom_stability=True)
        return self._build_action_result(
            description=f"Opened {self.page.url}",
            url_before=url_before,
            url_after=self.page.url,
            page_changed=normalize_url_for_fingerprint(url_before)
            != normalize_url_for_fingerprint(self.page.url),
            target_href=url,
        )

    async def go_back(self) -> dict[str, Any]:
        logger.info("Browser.go_back")
        url_before = self.page.url
        result = await self.page.go_back(timeout=NAVIGATION_TIMEOUT_MS)
        if result is None:
            raise RuntimeError("No previous page in browser history")
        await self._wait_for_stable(wait_for_dom_stability=True)
        return self._build_action_result(
            description=f"Returned to {self.page.url}",
            url_before=url_before,
            url_after=self.page.url,
            page_changed=normalize_url_for_fingerprint(url_before)
            != normalize_url_for_fingerprint(self.page.url),
        )

    async def click(self, element_id: int, elements: list[ElementSnapshot]) -> dict[str, Any]:
        target = self._find_element(element_id, elements)
        if target is None:
            raise ValueError(f"Element [{element_id}] not found in current observation")

        url_before = self.page.url
        dom_size_before = await self._capture_dom_size(self.page)
        pages_before = len(self.context.pages)
        target_href = str(target.get("href", ""))
        logger.info(
            f"Browser.click: element_id={element_id}, href={target_href or '(none)'}, pages_before={pages_before}"
        )
        try:
            locator = self.page.locator(f'[data-agent-ref="{element_id}"]')
            await locator.wait_for(state="visible", timeout=1500)
            await locator.click(timeout=5000, no_wait_after=True)
        except Exception as exc:
            x, y = self._center_for_element(element_id, elements)
            logger.debug(
                f"Browser.click: locator click failed, falling back to coordinates ({x}, {y}): {exc}"
            )
            await self.page.mouse.click(x, y)

        await self.page.wait_for_timeout(150)
        opened_new_tab = await self._adopt_new_tab_if_needed(previous_page_count=pages_before)
        url_after = self.page.url
        dom_size_after = await self._capture_dom_size(self.page)
        normalized_before = normalize_url_for_fingerprint(url_before)
        normalized_after = normalize_url_for_fingerprint(url_after)
        dom_changed = dom_size_before != dom_size_after
        page_changed = opened_new_tab or normalized_before != normalized_after or dom_changed

        if opened_new_tab or normalized_before != normalized_after:
            await self._wait_for_stable(wait_for_dom_stability=True)
            url_after = self.page.url
            normalized_after = normalize_url_for_fingerprint(url_after)
            page_changed = True
        elif dom_changed:
            await self._wait_for_stable(wait_for_dom_stability=True, render_buffer_ms=120)
        elif self._should_fallback_to_href(url_before=url_before, current_url=url_after, target_href=target_href):
            logger.info(f"Browser.click: no meaningful page change, navigating directly to href={target_href}")
            await self.page.goto(target_href, timeout=NAVIGATION_TIMEOUT_MS)
            await self._wait_for_stable(wait_for_dom_stability=True)
            url_after = self.page.url
            normalized_after = normalize_url_for_fingerprint(url_after)
            page_changed = normalized_before != normalized_after

        return self._build_action_result(
            description=f"Clicked element [{element_id}]",
            url_before=url_before,
            url_after=url_after,
            page_changed=page_changed,
            opened_new_tab=opened_new_tab,
            target_href=target_href,
        )

    async def type_text(
        self,
        element_id: int,
        text: str,
        elements: list[ElementSnapshot],
        *,
        press_enter: bool = False,
    ) -> dict[str, Any]:
        url_before = self.page.url
        dom_size_before = await self._capture_dom_size(self.page)
        logger.info(
            f"Browser.type_text: element_id={element_id}, chars={len(text)}, press_enter={press_enter}"
        )
        try:
            locator = self.page.locator(f'[data-agent-ref="{element_id}"]')
            await locator.wait_for(state="visible", timeout=1500)
            await locator.fill(text, timeout=5000)
        except Exception as exc:
            x, y = self._center_for_element(element_id, elements)
            logger.debug(
                f"Browser.type_text: locator fill failed, falling back to coordinates ({x}, {y}): {exc}"
            )
            await self.page.mouse.click(x, y, click_count=3)
            modifier = "Meta+A" if sys.platform == "darwin" else "Control+A"
            await self.page.keyboard.press(modifier)
            await self.page.keyboard.press("Backspace")
            await self.page.keyboard.type(text, delay=30)

        if press_enter:
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(150)
            dom_size_after = await self._capture_dom_size(self.page)
            page_changed = (
                normalize_url_for_fingerprint(url_before) != normalize_url_for_fingerprint(self.page.url)
                or dom_size_before != dom_size_after
            )
            if page_changed:
                await self._wait_for_stable(wait_for_dom_stability=True)
        else:
            page_changed = False

        return self._build_action_result(
            description=f"Typed {len(text)} characters into [{element_id}]",
            url_before=url_before,
            url_after=self.page.url,
            page_changed=page_changed,
        )

    async def press_key(self, key: str) -> dict[str, Any]:
        logger.info(f"Browser.press_key: key={key}")
        url_before = self.page.url
        dom_size_before = await self._capture_dom_size(self.page)
        await self.page.keyboard.press(key)
        if key in _NAVIGATION_KEYS:
            await self.page.wait_for_timeout(150)
            dom_size_after = await self._capture_dom_size(self.page)
            page_changed = (
                normalize_url_for_fingerprint(url_before) != normalize_url_for_fingerprint(self.page.url)
                or dom_size_before != dom_size_after
            )
            if page_changed:
                await self._wait_for_stable(wait_for_dom_stability=True)
        else:
            page_changed = False
        return self._build_action_result(
            description=f"Pressed key {key}",
            url_before=url_before,
            url_after=self.page.url,
            page_changed=page_changed,
        )

    async def scroll(self, direction: str, amount: int = 500) -> dict[str, Any]:
        if direction not in {"up", "down"}:
            raise ValueError(f"Invalid scroll direction: {direction!r}")
        logger.info(f"Browser.scroll: direction={direction}, amount={amount}")
        url_before = self.page.url
        delta = abs(amount)
        if direction == "up":
            delta = -delta
        await self.page.evaluate("(delta) => window.scrollBy(0, delta)", delta)
        await self.page.wait_for_timeout(200)
        scroll_y = await self.page.evaluate("window.scrollY")
        result = self._build_action_result(
            description=f"Scrolled {direction}",
            url_before=url_before,
            url_after=self.page.url,
            page_changed=False,
        )
        result["scroll_y"] = scroll_y
        return result

    async def wait(self, seconds: float = 2.0) -> dict[str, Any]:
        seconds = min(max(seconds, 0.0), 10.0)
        logger.info(f"Browser.wait: seconds={seconds:.1f}")
        url_before = self.page.url
        await self.page.wait_for_timeout(int(seconds * 1000))
        return self._build_action_result(
            description=f"Waited {seconds:.1f}s",
            url_before=url_before,
            url_after=self.page.url,
            page_changed=False,
        )

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
        url_before = self.page.url
        self.page = pages[index]
        await self.page.bring_to_front()
        await self._wait_for_stable(wait_for_dom_stability=False, render_buffer_ms=100)
        return self._build_action_result(
            description=f"Switched to tab #{index}",
            url_before=url_before,
            url_after=self.page.url,
            page_changed=normalize_url_for_fingerprint(url_before)
            != normalize_url_for_fingerprint(self.page.url),
            tab_index=index,
        )

    async def _wait_for_stable(
        self,
        *,
        wait_for_dom_stability: bool,
        render_buffer_ms: int = 150,
    ) -> None:
        logger.debug(
            "Browser._wait_for_stable: "
            f"timeout_ms={NAVIGATION_TIMEOUT_MS}, "
            f"wait_for_dom_stability={wait_for_dom_stability}, "
            f"render_buffer_ms={render_buffer_ms}"
        )
        await wait_for_page_ready(
            self.page,
            load_timeout_ms=NAVIGATION_TIMEOUT_MS,
            wait_for_dom_stability=wait_for_dom_stability,
            render_buffer_ms=render_buffer_ms,
        )

    def _center_for_element(self, element_id: int, elements: list[ElementSnapshot]) -> tuple[int, int]:
        element = self._find_element(element_id, elements)
        if element is None:
            raise ValueError(f"Element [{element_id}] not found in current observation")
        bbox = element.get("bbox")
        if bbox:
            return (
                int(bbox["x"] + bbox["width"] / 2),
                int(bbox["y"] + bbox["height"] / 2),
            )
        raise ValueError(f"Element [{element_id}] not found in current observation")

    def _find_element(self, element_id: int, elements: list[ElementSnapshot]) -> ElementSnapshot | None:
        for element in elements:
            ref = element.get("index", element.get("ref"))
            if ref == element_id:
                return element
        return None

    async def _capture_dom_size(self, page: Page) -> int:
        try:
            return int(
                await page.evaluate(
                    "() => (document.body || document.documentElement)?.innerHTML.length || 0"
                )
            )
        except Exception:
            return -1

    async def _adopt_new_tab_if_needed(self, *, previous_page_count: int) -> bool:
        if len(self.context.pages) <= previous_page_count:
            return False
        self.page = self.context.pages[-1]
        await self.page.bring_to_front()
        return True

    def _current_tab_index(self) -> int:
        for index, page in enumerate(self.context.pages):
            if page is self.page:
                return index
        return -1

    def _should_fallback_to_href(self, *, url_before: str, current_url: str, target_href: str) -> bool:
        if not target_href.startswith(("http://", "https://")):
            return False
        normalized_before = normalize_url_for_fingerprint(url_before)
        normalized_current = normalize_url_for_fingerprint(current_url)
        normalized_target = normalize_url_for_fingerprint(target_href)
        return normalized_current == normalized_before and normalized_target != normalized_before

    def _build_action_result(
        self,
        *,
        description: str,
        url_before: str,
        url_after: str,
        page_changed: bool,
        opened_new_tab: bool = False,
        target_href: str = "",
        tab_index: int | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "description": description,
            "url_before": url_before,
            "url_after": url_after,
            "page_changed": page_changed,
            "opened_new_tab": opened_new_tab,
            "tab_index": self._current_tab_index() if tab_index is None else tab_index,
            "target_href": target_href,
        }

    def _to_snapshot(self, element: InteractiveElement) -> ElementSnapshot:
        snapshot = ElementSnapshot(**asdict(element))
        snapshot["index"] = element.ref
        return snapshot
