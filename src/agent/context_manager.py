import re
from typing import Any

MAX_MESSAGES = 40


class ContextManager:
    def _is_screenshot_message(self, msg: dict[str, Any]) -> bool:
        content = msg.get("content")
        if not isinstance(content, list):
            return False
        return any(isinstance(p, dict) and p.get("type") == "image_url" for p in content)

    def prepare(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system_msg = messages[0]
        task_msg = messages[1]
        body = list(messages[2:])

        # Drop old screenshots first (keep only the last one) — they're token-heavy
        screenshot_indices = [i for i, m in enumerate(body) if self._is_screenshot_message(m)]
        if len(screenshot_indices) > 1:
            to_drop = set(screenshot_indices[:-1])
            body = [m for i, m in enumerate(body) if i not in to_drop]

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
