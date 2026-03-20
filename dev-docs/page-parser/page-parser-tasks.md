# Tasks: Page Parser

## Stage 1: Dataclasses + skeleton

- [ ] Add `InteractiveElement` dataclass to `src/parser/page_parser.py` (fields: ref, tag, role, text, aria_label, placeholder, href, input_type, value, disabled)
- [ ] Add `PageState` dataclass (fields: url, title, content, elements)
- [ ] Add empty async stub `extract_page_state(page: Page) -> PageState`
- [ ] Add empty `format_page_state(url, title, elements, text_content, max_elements=150) -> str`
- [ ] Add proper imports: `dataclasses`, `playwright.async_api.Page`
- [ ] Run `ruff check --fix` + `ruff format` + `pyright` — zero errors
- [ ] Commit: "feat: add PageState and InteractiveElement dataclasses"

## Stage 2: JS element extraction

- [ ] Implement JS pass 1 in `extract_page_state`: `querySelectorAll` with all 14 selector groups
- [ ] Filter invisible elements (`width==0`, `visibility:hidden`, `display:none`)
- [ ] Filter far-offscreen elements (`rect.top > innerHeight+500` or `rect.bottom < -500`)
- [ ] Assign `data-agent-ref=N` to each surviving element
- [ ] Collect fields: ref, tag, role, text (80), aria_label, placeholder, href (120), type, value (50), disabled
- [ ] Deduplicate with JS `Set`, cap at 150 elements
- [ ] Parse JS result into `list[InteractiveElement]` in Python
- [ ] Run `ruff check --fix` + `ruff format` + `pyright` — zero errors
- [ ] Commit: "feat: implement interactive element extraction via JS evaluate"

## Stage 3: Text content + formatting + main.py test

- [ ] Implement JS pass 2 in `extract_page_state`: TreeWalker over text nodes
- [ ] Skip script/style/noscript/svg ancestors
- [ ] Skip invisible nodes (`offsetParent === null`)
- [ ] Accumulate up to 4000 chars, join with `" | "`
- [ ] Implement `format_page_state`: produce `## Current Page / ## Page Content / ## Interactive Elements` sections
- [ ] Element line logic: role mapping, label, href suffix, value suffix, disabled marker
- [ ] Wire up `extract_page_state` to call `format_page_state` and return complete `PageState`
- [ ] Update `src/main.py`: navigate to `https://www.google.com`, call `extract_page_state`, print `state.content`
- [ ] Remove unused `ainput` import from `src/main.py`
- [ ] Run `uv run python -m src.main` — verify output has numbered elements, search input, buttons, total < 5000 chars
- [ ] Run `ruff check --fix` + `ruff format` + `pyright` — zero errors
- [ ] Commit: "feat: implement page parser with text extraction and formatted output"
