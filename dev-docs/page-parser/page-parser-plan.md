# Plan: Page Parser Implementation

## Goal

Implement `src/parser/page_parser.py` — the perception layer of the AI browser agent. The parser extracts interactive elements from the live DOM (assigning `data-agent-ref=N` refs so the LLM can target them), captures visible text content, and formats everything into a compact ~8K-token string that GPT-4.1 receives before each decision step. Without this module, the agent is blind.

## Current State

- `src/parser/page_parser.py` — single-line stub (line 1: comment only)
- `src/main.py` (lines 1–19) — launches browser, opens blank tab, waits for Enter; no page state extraction
- `src/browser/controller.py` — fully implemented; provides `launch_browser()`, `wait_for_page_ready()`, `close_browser()`
- No dataclasses defined anywhere in the project yet

## Proposed Approach

### Step 1: Define dataclasses

```python
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
    content: str          # result of format_page_state()
    elements: list[InteractiveElement]
```

### Step 2: `extract_page_state(page: Page) -> PageState`

**JS pass 1 — interactive elements:**
- Single `querySelectorAll` with all 14 selector groups combined via comma
- Selectors: `a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [role="checkbox"], [role="radio"], [onclick], [tabindex]:not([tabindex="-1"]), summary, details`
- Dedup with a JS `Set` (some elements match multiple selectors)
- Per element: call `getBoundingClientRect()` + `getComputedStyle()`
- **Skip if**: `rect.width === 0`, `style.visibility === 'hidden'`, `style.display === 'none'`
- **Skip if**: `rect.top > window.innerHeight + 500` OR `rect.bottom < -500`
- Assign `element.dataset.agentRef = String(ref)` and increment counter
- Collect up to **150 elements**; return array of plain objects

**JS pass 2 — text content:**
- `document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)`
- For each text node: walk up ancestors, skip if any ancestor is `SCRIPT`, `STYLE`, `NOSCRIPT`, `SVG`
- Skip if `parentElement.offsetParent === null` (invisible)
- Accumulate `node.textContent.trim()`, skip empty strings
- Join fragments with `" | "`, stop at **4000 chars**

### Step 3: `format_page_state(url, title, elements, text_content, max_elements=150) -> str`

Output:
```
## Current Page
URL: {url}
Title: {title}

## Page Content (summary)
{text_content}

## Interactive Elements
[0] link "Gmail" → https://mail.google.com/
[1] button "Search"
[2] input[text] "Query" value=""
[3] select "Country" [disabled]
```

Element line logic:
- Display role: `element.role` if set, else tag map: `a→link`, `button→button`, `input→input[{input_type}]`, `select→select`, `textarea→textarea`, else tag
- Label: `aria_label or text or placeholder` in `"..."` (empty string if none)
- Append ` → {href}` if href
- Append ` value="{value}"` if value
- Append ` [disabled]` if disabled

### Step 4: Update `src/main.py` for testing

```python
await page.goto("https://www.google.com")
await wait_for_page_ready(page)
state = await extract_page_state(page)
print(state.content)
```

Remove `ainput` import (no longer needed). Keep `close_browser` in `finally`.

## Alternatives Considered

1. **Use Playwright's built-in locators** (`page.locator(...).all()`) instead of raw JS — cleaner Python API but requires N round-trips (one per element), ~10–50x slower than a single `evaluate()` call. Rejected for performance.

2. **Accessibility tree extraction** via `page.accessibility.snapshot()` — gives semantic role info for free but misses many interactive elements (custom components, onclick-only divs) and Playwright marks it experimental. Rejected for completeness.

3. **Screenshot + vision only** — zero DOM parsing, just send screenshot to GPT-4V. Too expensive per step, loses precise element refs. Planned as fallback (step 10 in CLAUDE.md), not primary.

## Key Decisions

- **Single `evaluate()` per pass**: all DOM work in one JS call to minimize Playwright round-trips
- **`data-agent-ref`**: mutates the DOM, but this is acceptable — it enables reliable element targeting across steps
- **`content` field on `PageState` stores the formatted string**: avoids re-formatting on every read; `elements` kept separately for tool use
- **150 element cap**: prevents token overflow; most pages have fewer interactive elements above the fold

## Risks & Edge Cases

- **Shadow DOM**: elements inside shadow roots won't be found by `querySelectorAll` at document level — acceptable for MVP
- **Iframes**: cross-origin iframes are inaccessible — acceptable for MVP
- **Dynamic pages**: refs assigned at extraction time; if DOM mutates before action, ref may be stale — handled by re-calling `extract_page_state` before each LLM step
- **Pages without `document.body`** (e.g., XML, PDF viewer): TreeWalker pass will fail — wrap in try/catch, return empty content
- **Very long href/text**: truncated at source (href: 120 chars, text: 80 chars, value: 50 chars)

## Dependencies

- `playwright.async_api.Page` — for `page.evaluate()`, `page.url`, `page.title()`
- Python stdlib: `dataclasses`, `asyncio` (none beyond what's already in the project)

## Definition of Done

- `uv run ruff check src/ --fix` — zero errors
- `uv run ruff format src/` — no changes
- `uv run pyright src/` — zero errors
- `uv run python -m src.main` output:
  - Contains `## Interactive Elements` header
  - Google search input shows as `input[text]`
  - At least one button present
  - Ref numbers `[0]`, `[1]`, ... visible
  - Total `state.content` length < 5000 characters
