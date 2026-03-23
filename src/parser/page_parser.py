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
class Viewport:
    width: int
    height: int


@dataclass
class ActiveModal:
    kind: str
    label: str
    bbox: BBox | None = None


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
    center_x: int | None = None
    center_y: int | None = None
    in_modal: bool = False


@dataclass
class PageState:
    url: str
    title: str
    content: str
    elements: list[InteractiveElement]
    viewport: Viewport | None = None
    active_modal: ActiveModal | None = None


@dataclass
class PageStateWithScreenshot:
    page_state: PageState
    screenshot_b64: str


_JS_EXTRACT_ELEMENTS = """
() => {
    for (const stale of document.querySelectorAll('[data-agent-ref]')) {
        delete stale.dataset.agentRef;
    }

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
    const viewport = {
        width: Math.round(window.innerWidth || 0),
        height: Math.round(window.innerHeight || 0),
    };

    const isVisible = (el) => {
        if (!(el instanceof Element)) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width < 5 || rect.height < 5) return false;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return false;
        return rect.bottom >= 0
            && rect.right >= 0
            && rect.top <= window.innerHeight
            && rect.left <= window.innerWidth;
    };

    const getLabel = (el) => {
        const ariaLabel = (el.getAttribute('aria-label') || '').trim();
        if (ariaLabel) return ariaLabel.slice(0, 120);

        const labelledBy = (el.getAttribute('aria-labelledby') || '').trim();
        if (labelledBy) {
            const text = labelledBy
                .split(/\\s+/)
                .map((id) => document.getElementById(id)?.textContent || '')
                .join(' ')
                .trim();
            if (text) return text.slice(0, 120);
        }

        const heading = el.querySelector('h1, h2, h3, h4, [role="heading"]');
        const headingText = (heading?.textContent || '').trim();
        if (headingText) return headingText.slice(0, 120);

        return (el.textContent || '').trim().slice(0, 120);
    };

    const activeSurfaceSelectors = [
        '[role="dialog"]',
        '[role="alertdialog"]',
        '[role="listbox"]',
        '[role="menu"]',
        '[aria-modal="true"]',
    ];
    let active_modal = null;

    for (const selector of activeSurfaceSelectors) {
        let elements;
        try {
            elements = document.querySelectorAll(selector);
        } catch (e) {
            continue;
        }
        for (const el of elements) {
            if (!isVisible(el)) continue;
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            const zIndex = Number.parseInt(style.zIndex || '0', 10) || 0;
            const area = Math.round(rect.width * rect.height);
            const candidate = {
                kind: (
                    el.getAttribute('role')
                    || (el.getAttribute('aria-modal') === 'true' ? 'dialog' : 'surface')
                ).slice(0, 40),
                label: getLabel(el),
                bbox: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                },
                z_index: zIndex,
                area,
            };
            if (
                !active_modal
                || candidate.z_index > active_modal.z_index
                || (candidate.z_index === active_modal.z_index && candidate.area > active_modal.area)
            ) {
                active_modal = candidate;
            }
        }
    }

    const modalRect = active_modal?.bbox || null;
    const isInsideModal = (cx, cy) => {
        if (!modalRect) return false;
        return cx >= modalRect.x && cx <= modalRect.x + modalRect.width
            && cy >= modalRect.y && cy <= modalRect.y + modalRect.height;
    };

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

            if (!isVisible(el)) continue;
            const rect = el.getBoundingClientRect();

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
                center_x: Math.round(rect.x + rect.width / 2),
                center_y: Math.round(rect.y + rect.height / 2),
                in_modal: isInsideModal(Math.round(rect.x + rect.width / 2), Math.round(rect.y + rect.height / 2)),
            });
            ref++;
            if (results.length >= 150) break;
        }
        if (results.length >= 150) break;
    }

    if (active_modal) {
        delete active_modal.z_index;
        delete active_modal.area;
    }

    return {
        elements: results,
        viewport,
        active_modal,
    };
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
    *,
    viewport: Viewport | None = None,
    active_modal: ActiveModal | None = None,
    max_elements: int = 150,
) -> str:
    parts = [
        "## Current Page",
        f"URL: {url}",
        f"Title: {title}",
    ]
    if viewport is not None:
        parts.append(f"Viewport: {viewport.width}x{viewport.height}px")
    if active_modal is not None:
        label_suffix = f' "{active_modal.label}"' if active_modal.label else ""
        parts.append(f"Active modal: {active_modal.kind}{label_suffix}")
    parts.extend(
        [
            "",
            "## Page Content (summary)",
            text_content or "(no visible text)",
            "",
            "## Interactive Elements",
        ]
    )
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
        raw_payload = await page.evaluate(_JS_EXTRACT_ELEMENTS)
    except PlaywrightError:
        raw_payload = {"elements": [], "viewport": None, "active_modal": None}

    if isinstance(raw_payload, dict):
        raw_elements: list[dict] = list(raw_payload.get("elements", []))
        raw_viewport = raw_payload.get("viewport")
        raw_active_modal = raw_payload.get("active_modal")
    else:
        raw_elements = list(raw_payload) if isinstance(raw_payload, list) else []
        raw_viewport = None
        raw_active_modal = None

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
            center_x=e.get("center_x"),
            center_y=e.get("center_y"),
            in_modal=bool(e.get("in_modal", False)),
        )
        for e in raw_elements
    ]

    viewport = (
        Viewport(
            width=int(raw_viewport["width"]),
            height=int(raw_viewport["height"]),
        )
        if isinstance(raw_viewport, dict)
        and raw_viewport.get("width") is not None
        and raw_viewport.get("height") is not None
        else None
    )

    active_modal = None
    if isinstance(raw_active_modal, dict):
        modal_bbox = raw_active_modal.get("bbox")
        active_modal = ActiveModal(
            kind=str(raw_active_modal.get("kind", "")).strip() or "surface",
            label=str(raw_active_modal.get("label", "")).strip(),
            bbox=(
                BBox(
                    x=modal_bbox["x"],
                    y=modal_bbox["y"],
                    width=modal_bbox["width"],
                    height=modal_bbox["height"],
                )
                if isinstance(modal_bbox, dict)
                else None
            ),
        )

    try:
        text_content: str = await page.evaluate(_JS_EXTRACT_TEXT)
    except PlaywrightError:
        text_content = ""

    content = format_page_state(
        url,
        title,
        elements,
        text_content,
        viewport=viewport,
        active_modal=active_modal,
    )
    return PageState(
        url=url,
        title=title,
        content=content,
        elements=elements,
        viewport=viewport,
        active_modal=active_modal,
    )


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
