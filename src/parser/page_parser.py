import asyncio
import base64
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page


@dataclass
class BBox:
    x: int
    y: int
    width: int
    height: int


@dataclass
class InteractiveElement:
    ref: int
    tag: str
    role: str
    text: str
    aria_label: str
    placeholder: str
    href: str
    name: str
    input_type: str
    value: str
    disabled: bool
    bbox: BBox | None = None


@dataclass
class PageState:
    url: str
    title: str
    content: str
    elements: list[InteractiveElement]


@dataclass
class PageStateWithScreenshot:
    page_state: PageState
    screenshot_b64: str


_JS_EXTRACT_ELEMENTS = """
() => {
    const selectors = [
        '[role="option"]', '[role="listbox"]',
        'a[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea',
        '[role="button"]', '[role="link"]', '[role="menuitem"]',
        '[role="tab"]', '[role="checkbox"]', '[role="radio"]',
        '[role="switch"]', '[role="combobox"]', '[role="searchbox"]',
        '[onclick]', '[tabindex="0"]', 'summary', 'label[for]'
    ];
    const seen = new Set();
    const results = [];
    let ref = 0;

    for (const selector of selectors) {
        let elements;
        try {
            elements = document.querySelectorAll(selector);
        } catch (e) {
            continue;
        }
        for (const el of elements) {
            if (seen.has(el)) continue;
            seen.add(el);

            const rect = el.getBoundingClientRect();
            if (rect.width < 5 || rect.height < 5) continue;
            const style = getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            if (rect.top > window.innerHeight || rect.bottom < 0) continue;
            if (rect.left > window.innerWidth || rect.right < 0) continue;

            el.dataset.agentRef = String(ref);

            const text = (el.textContent || '').trim().slice(0, 200);
            const href = (el.getAttribute('href') || '').slice(0, 200);
            const absHref = href
                ? (() => { try { return new URL(href, location.href).href; } catch { return href; } })()
                : '';

            results.push({
                ref,
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                text,
                aria_label: el.getAttribute('aria-label') || '',
                placeholder: el.getAttribute('placeholder') || '',
                href: absHref,
                name: el.getAttribute('name') || '',
                input_type: el.getAttribute('type') || '',
                value: (el.value !== undefined ? String(el.value) : '').slice(0, 50),
                disabled: el.disabled === true || el.getAttribute('disabled') !== null,
                bbox: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                },
            });
            ref++;
            if (results.length >= 150) break;
        }
        if (results.length >= 150) break;
    }
    return results;
}
"""

_JS_EXTRACT_TEXT = """
() => {
    const skipTags = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG']);
    const walker = document.createTreeWalker(
        document.body || document.documentElement,
        NodeFilter.SHOW_TEXT
    );
    const fragments = [];
    let total = 0;
    let node;
    while ((node = walker.nextNode())) {
        const text = node.textContent.trim();
        if (!text) continue;
        let ancestor = node.parentElement;
        let skip = false;
        while (ancestor) {
            if (skipTags.has(ancestor.tagName)) { skip = true; break; }
            ancestor = ancestor.parentElement;
        }
        if (skip) continue;
        if (node.parentElement && node.parentElement.offsetParent === null) continue;
        const remaining = 4000 - total;
        if (remaining <= 0) break;
        const chunk = text.length <= remaining ? text : text.slice(0, remaining);
        fragments.push(chunk);
        total += chunk.length + 3;
        if (total >= 4000) break;
    }
    return fragments.join(' | ');
}
"""


def _format_element(el: InteractiveElement) -> str:
    tag_map = {
        "a": "link",
        "button": "button",
        "select": "select",
        "textarea": "textarea",
    }
    if el.role:
        display_role = el.role
    elif el.tag == "input":
        display_role = f"input[{el.input_type or 'text'}]"
    else:
        display_role = tag_map.get(el.tag, el.tag)

    label = el.aria_label or el.text or el.placeholder
    line = f'[{el.ref}] {display_role} "{label}"'
    if el.href:
        line += f" → {el.href}"
    if el.value:
        line += f' value="{el.value}"'
    if el.disabled:
        line += " [disabled]"
    return line


def format_page_state(
    url: str,
    title: str,
    elements: list[InteractiveElement],
    text_content: str,
    max_elements: int = 150,
) -> str:
    parts = [
        "## Current Page",
        f"URL: {url}",
        f"Title: {title}",
        "",
        "## Page Content (summary)",
        text_content or "(no visible text)",
        "",
        "## Interactive Elements",
    ]
    for el in elements[:max_elements]:
        parts.append(_format_element(el))
    return "\n".join(parts)


async def extract_page_state(page: Page) -> PageState:
    url = page.url
    try:
        title = await page.title()
    except PlaywrightError:
        title = ""

    try:
        raw_elements: list[dict] = await page.evaluate(_JS_EXTRACT_ELEMENTS)
    except PlaywrightError:
        raw_elements = []

    elements = [
        InteractiveElement(
            ref=e["ref"],
            tag=e["tag"],
            role=e["role"],
            text=e["text"],
            aria_label=e["aria_label"],
            placeholder=e["placeholder"],
            href=e["href"],
            name=e["name"],
            input_type=e["input_type"],
            value=e["value"],
            disabled=e["disabled"],
            bbox=(
                BBox(
                    x=e["bbox"]["x"],
                    y=e["bbox"]["y"],
                    width=e["bbox"]["width"],
                    height=e["bbox"]["height"],
                )
                if e.get("bbox")
                else None
            ),
        )
        for e in raw_elements
    ]

    try:
        text_content: str = await page.evaluate(_JS_EXTRACT_TEXT)
    except PlaywrightError:
        text_content = ""

    content = format_page_state(url, title, elements, text_content)
    return PageState(url=url, title=title, content=content, elements=elements)


async def take_screenshot(page: Page, quality: int = 65) -> str:
    try:
        data = await page.screenshot(type="jpeg", quality=quality, full_page=False)
    except Exception:
        return ""
    return base64.b64encode(data).decode("utf-8")


async def extract_page_state_with_screenshot(page: Page) -> PageStateWithScreenshot:
    page_state, screenshot_b64 = await asyncio.gather(
        extract_page_state(page),
        take_screenshot(page),
    )
    return PageStateWithScreenshot(page_state=page_state, screenshot_b64=screenshot_b64)
