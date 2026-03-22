from __future__ import annotations

import json

from openai import AsyncOpenAI

from src.agent.prompts import FIND_ELEMENT_PROMPT, PAGE_ANALYZER_PROMPT
from src.agent.state import ElementSnapshot
from src.config.settings import TEMPERATURE, VISION_MODEL
from src.utils.logger import logger


class PageAnalyzer:
    def __init__(self, client: AsyncOpenAI | None) -> None:
        self._client = client

    async def analyze_page(
        self,
        *,
        screenshot_b64: str,
        elements: list[ElementSnapshot],
        url: str,
        title: str,
    ) -> str:
        if self._client is None or not screenshot_b64:
            summary = self._fallback_summary(elements, url, title)
            logger.debug(
                f"PageAnalyzer.analyze_page: using fallback summary for url={url}, elements={len(elements)}"
            )
            return summary

        element_preview = self._serialize_elements(elements)
        try:
            response = await self._client.chat.completions.create(
                model=VISION_MODEL,
                temperature=TEMPERATURE,
                messages=[
                    {"role": "system", "content": PAGE_ANALYZER_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"URL: {url}\nTitle: {title}\n"
                                    f"Interactive elements:\n{element_preview}"
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{screenshot_b64}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
            )
        except Exception as exc:
            logger.warning(f"PageAnalyzer.analyze_page: model call failed, using fallback summary: {exc}")
            return self._fallback_summary(elements, url, title)

        summary = (response.choices[0].message.content or "").strip()
        logger.debug(f"PageAnalyzer.analyze_page summary: {summary[:500]}")
        return summary or self._fallback_summary(elements, url, title)

    async def find_element(
        self,
        *,
        screenshot_b64: str,
        elements: list[ElementSnapshot],
        question: str,
    ) -> str:
        if self._client is None or not screenshot_b64:
            logger.debug("PageAnalyzer.find_element: using fallback search")
            return self._fallback_find(elements, question)

        try:
            response = await self._client.chat.completions.create(
                model=VISION_MODEL,
                temperature=TEMPERATURE,
                messages=[
                    {"role": "system", "content": FIND_ELEMENT_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Question: {question}\n"
                                    f"Elements:\n{self._serialize_elements(elements)}"
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{screenshot_b64}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
            )
        except Exception as exc:
            logger.warning(f"PageAnalyzer.find_element: model call failed, using fallback search: {exc}")
            return self._fallback_find(elements, question)

        answer = (response.choices[0].message.content or "").strip()
        logger.debug(f"PageAnalyzer.find_element answer: {answer[:300]}")
        return answer or self._fallback_find(elements, question)

    def _serialize_elements(self, elements: list[ElementSnapshot]) -> str:
        preview = []
        for element in elements[:40]:
            preview.append(
                json.dumps(
                    {
                        "id": element.get("index", element.get("ref")),
                        "tag": element.get("tag"),
                        "role": element.get("role"),
                        "text": element.get("text"),
                        "aria_label": element.get("aria_label"),
                        "placeholder": element.get("placeholder"),
                        "href": element.get("href"),
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(preview)

    def _fallback_summary(
        self, elements: list[ElementSnapshot], url: str, title: str
    ) -> str:
        labels = []
        for element in elements[:8]:
            label = (
                element.get("aria_label")
                or element.get("text")
                or element.get("placeholder")
                or "(без подписи)"
            )
            labels.append(f'[{element.get("index", element.get("ref", "?"))}] {label}')

        summary = f'Страница "{title}" ({url}). Найдено {len(elements)} интерактивных элементов.'
        if labels:
            summary += " В видимой области: " + ", ".join(labels[:5]) + "."
        return summary

    def _fallback_find(self, elements: list[ElementSnapshot], question: str) -> str:
        lower_question = question.lower()
        for element in elements:
            haystack = " ".join(
                [
                    str(element.get("text", "")),
                    str(element.get("aria_label", "")),
                    str(element.get("placeholder", "")),
                ]
            ).lower()
            if haystack and any(token in haystack for token in lower_question.split()):
                return (
                    f'Вероятно подходит [{element.get("index", element.get("ref", "?"))}] '
                    f'"{element.get("text") or element.get("aria_label") or element.get("placeholder")}".'
                )
        return "Подходящий элемент по вопросу не найден."
