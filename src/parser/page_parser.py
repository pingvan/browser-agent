from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page


@dataclass
class InteractiveElement:
    ref: int
    tag: str
    role: str
    text: str
    aria_label: str
    placeholder: str
    href: str
    input_type: str
    value: str
    disabled: bool


@dataclass
class PageState:
    url: str
    title: str
    content: str
    elements: list[InteractiveElement]


_JS_EXTRACT_ELEMENTS = """
() => {
    const selectors = [
        'a[href]', 'button', 'input', 'select', 'textarea',
        '[role="button"]', '[role="link"]', '[role="tab"]',
        '[role="menuitem"]', '[role="checkbox"]', '[role="radio"]',
        '[onclick]', '[tabindex]:not([tabindex="-1"])', 'summary'
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
            if (rect.width === 0) continue;
            const style = getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            if (rect.top > window.innerHeight + 500 || rect.bottom < -500) continue;

            el.dataset.agentRef = String(ref);

            const text = (el.textContent || '').trim().slice(0, 80);
            const href = (el.getAttribute('href') || '').slice(0, 120);
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
                input_type: el.getAttribute('type') || '',
                value: (el.value !== undefined ? String(el.value) : '').slice(0, 50),
                disabled: el.disabled === true || el.getAttribute('disabled') !== null,
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
        fragments.push(text);
        total += text.length + 3;
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
            input_type=e["input_type"],
            value=e["value"],
            disabled=e["disabled"],
        )
        for e in raw_elements
    ]

    try:
        text_content: str = await page.evaluate(_JS_EXTRACT_TEXT)
    except PlaywrightError:
        text_content = ""

    content = format_page_state(url, title, elements, text_content)
    return PageState(url=url, title=title, content=content, elements=elements)
