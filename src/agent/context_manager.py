import re
from typing import Any

MAX_MESSAGES = 40


class ContextManager:
    MAX_SCREENSHOTS_KEPT = 1

    def _has_screenshot(self, msg: dict[str, Any]) -> bool:
        content = msg.get("content")
        if not isinstance(content, list):
            return False
        return any(isinstance(p, dict) and p.get("type") == "image_url" for p in content)

    def _strip_screenshot(self, msg: dict[str, Any]) -> dict[str, Any]:
        content = msg.get("content")
        if not isinstance(content, list):
            return msg

        stripped_content: list[Any] = []
        replaced = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                stripped_content.append(
                    {
                        "type": "text",
                        "text": "[Screenshot removed — outdated. Refer to the latest screenshot above.]",
                    }
                )
                replaced = True
                continue
            stripped_content.append(part)

        if not replaced:
            return msg

        return {**msg, "content": stripped_content}

    def prepare(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system_msg = messages[0]
        task_msg = messages[1]
        body = list(messages[2:])

        screenshot_indices = [i for i, m in enumerate(body) if self._has_screenshot(m)]
        if len(screenshot_indices) > self.MAX_SCREENSHOTS_KEPT:
            to_strip = set(screenshot_indices[: -self.MAX_SCREENSHOTS_KEPT])
            body = [
                self._strip_screenshot(message) if i in to_strip else message
                for i, message in enumerate(body)
            ]

        if len(body) + 2 <= MAX_MESSAGES:
            return [system_msg, task_msg] + body

        removed = len(body) - MAX_MESSAGES
        context_note: dict[str, Any] = {
            "role": "user",
            "content": (
                f"[Context note: {removed} earlier messages were trimmed. "
                "The task and recent actions are preserved.]"
            ),
        }
        return [system_msg, task_msg, context_note] + body[-MAX_MESSAGES:]

    def truncate_page_state(self, content: str, budget: int = 32_000) -> str:
        if len(content) <= budget:
            return content

        header_match = re.search(r"^(## Current Page.*?)(?=## Page Content)", content, re.DOTALL)
        header = header_match.group(1) if header_match else ""

        elements_match = re.search(r"(## Interactive Elements.*)", content, re.DOTALL)
        if elements_match:
            elements_block = elements_match.group(1)
            lines = elements_block.split("\n")
            heading = lines[0]
            element_lines = [line for line in lines[1:] if line.strip()]
            truncated_elements = element_lines[:100]
            if len(element_lines) > 100:
                truncated_elements.append(
                    f"\n[... {len(element_lines) - 100} more elements truncated]"
                )
            elements_section = heading + "\n" + "\n".join(truncated_elements)
        else:
            elements_section = ""

        used = len(header) + len(elements_section)
        remaining_budget = max(budget - used - 50, 500)

        page_content_match = re.search(
            r"(## Page Content.*?)(?=## Interactive Elements)", content, re.DOTALL
        )
        if page_content_match:
            page_content = page_content_match.group(1)
            if len(page_content) > remaining_budget:
                page_content = page_content[:remaining_budget] + "\n[... content truncated]"
        else:
            page_content = ""

        return header + page_content + elements_section
