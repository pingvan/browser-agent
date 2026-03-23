from __future__ import annotations

import json

from openai import AsyncOpenAI

from src.agent.prompts import DOM_INSPECTOR_PROMPT
from src.agent.state import ElementSnapshot, InspectionCandidate, InspectionResult
from src.config.settings import SUMMARY_MODEL, TEMPERATURE
from src.parser.page_parser import PageState
from src.utils.logger import logger


class DomInspector:
    def __init__(self, client: AsyncOpenAI | None) -> None:
        self._client = client

    async def inspect(
        self,
        *,
        question: str,
        page_state: PageState,
        elements: list[ElementSnapshot],
        fingerprint: str,
    ) -> InspectionResult:
        if self._client is None:
            return self._fallback_result(
                question=question,
                page_state=page_state,
                elements=elements,
                fingerprint=fingerprint,
                source="dom",
            )

        user_text = (
            f"Question: {question}\n"
            f"URL: {page_state.url}\n"
            f"Title: {page_state.title}\n"
            f"Visible text:\n{self._extract_visible_text(page_state.content)}\n"
            f"Interactive elements:\n{self._serialize_elements(elements)}"
        )
        try:
            response = await self._client.chat.completions.create(
                model=SUMMARY_MODEL,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": DOM_INSPECTOR_PROMPT},
                    {"role": "user", "content": user_text},
                ],
            )
        except Exception as exc:
            logger.warning(f"DomInspector.inspect: model call failed, using fallback: {exc}")
            return self._fallback_result(
                question=question,
                page_state=page_state,
                elements=elements,
                fingerprint=fingerprint,
                source="dom",
            )

        return self._parse_result(
            raw=response.choices[0].message.content or "{}",
            question=question,
            fingerprint=fingerprint,
            source="dom",
            fallback_page_state=page_state,
            fallback_elements=elements,
        )

    def _fallback_result(
        self,
        *,
        question: str,
        page_state: PageState,
        elements: list[ElementSnapshot],
        fingerprint: str,
        source: str,
    ) -> InspectionResult:
        observations = []
        if page_state.title:
            observations.append(f"Title: {page_state.title[:120]}")
        visible_text = self._extract_visible_text(page_state.content)
        if visible_text:
            observations.append(visible_text[:180])
        return InspectionResult(
            question=question[:200],
            answer=(visible_text or page_state.title or "No additional DOM insight available.")[:240],
            observations=observations[:4],
            candidate_elements=self._fallback_candidates(elements),
            source="dom" if source == "dom" else "vision",
            fingerprint=fingerprint,
        )

    def _parse_result(
        self,
        *,
        raw: str,
        question: str,
        fingerprint: str,
        source: str,
        fallback_page_state: PageState,
        fallback_elements: list[ElementSnapshot],
    ) -> InspectionResult:
        logger.debug(f"{source.capitalize()}Inspector raw response: {raw[:1200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._fallback_result(
                question=question,
                page_state=fallback_page_state,
                elements=fallback_elements,
                fingerprint=fingerprint,
                source=source,
            )

        return InspectionResult(
            question=question[:200],
            answer=str(data.get("answer", "")).strip()[:320],
            observations=self._clean_list(data.get("observations", [])),
            candidate_elements=self._clean_candidates(data.get("candidate_elements", [])),
            source="dom" if source == "dom" else "vision",
            fingerprint=fingerprint,
        )

    def _serialize_elements(self, elements: list[ElementSnapshot]) -> str:
        lines = []
        for element in elements[:30]:
            lines.append(
                json.dumps(
                    {
                        "element_id": element.get("index", element.get("ref")),
                        "role": element.get("role") or element.get("tag"),
                        "text": element.get("text"),
                        "aria_label": element.get("aria_label"),
                        "href": element.get("href"),
                        "value": element.get("value"),
                        "disabled": element.get("disabled"),
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(lines)

    def _extract_visible_text(self, page_content: str) -> str:
        if not page_content:
            return ""
        marker = "## Page Content (summary)"
        start = page_content.find(marker)
        if start == -1:
            return " ".join(page_content.split())[:800]
        text = page_content[start + len(marker) :].strip()
        next_marker = text.find("## Interactive Elements")
        if next_marker != -1:
            text = text[:next_marker]
        return " ".join(text.split())[:800]

    def _clean_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items = []
        for item in value[:4]:
            text = str(item).strip()
            if text:
                items.append(text[:180])
        return items

    def _clean_candidates(self, value: object) -> list[InspectionCandidate]:
        if not isinstance(value, list):
            return []
        candidates: list[InspectionCandidate] = []
        for item in value[:6]:
            if not isinstance(item, dict):
                continue
            element_id = item.get("element_id")
            reason = str(item.get("reason", "")).strip()
            if isinstance(element_id, int) and reason:
                candidates.append(InspectionCandidate(element_id=element_id, reason=reason[:180]))
        return candidates

    def _fallback_candidates(self, elements: list[ElementSnapshot]) -> list[InspectionCandidate]:
        candidates: list[InspectionCandidate] = []
        for element in elements[:4]:
            element_id = element.get("index", element.get("ref"))
            label = (
                element.get("aria_label")
                or element.get("text")
                or element.get("placeholder")
                or ""
            ).strip()
            if isinstance(element_id, int) and label:
                candidates.append(
                    InspectionCandidate(
                        element_id=element_id,
                        reason=f'Visible element "{label[:120]}"',
                    )
                )
        return candidates
