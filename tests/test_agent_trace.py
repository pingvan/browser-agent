import unittest

from src.agent.trace import (
    build_step_result_log,
    build_step_start_log,
    format_tool_arguments,
)
from src.parser.page_parser import InteractiveElement, PageState


def _build_page_state(*elements: InteractiveElement, url: str = "https://example.com") -> PageState:
    return PageState(
        url=url,
        title="Example Page",
        content="Example content",
        elements=list(elements),
    )


class AgentTraceTests(unittest.TestCase):
    def test_click_step_includes_human_readable_target(self) -> None:
        before_state = _build_page_state(
            InteractiveElement(
                ref=120,
                tag="a",
                role="",
                text="Печенье Milka XL Cookies с шоколадом 138 г",
                aria_label="",
                placeholder="",
                href="https://example.com/cookies/milka",
                name="",
                input_type="",
                value="",
                disabled=False,
            )
        )

        log_line = build_step_start_log(
            step=5,
            fn_name="click",
            args={"ref": 120},
            before_state=before_state,
            model_note="Открываю карточку товара, чтобы посмотреть детали.",
        )

        self.assertIn("Step 5", log_line)
        self.assertIn("Tool: click({\"ref\": 120})", log_line)
        self.assertIn('[120] link "Печенье Milka XL Cookies с шоколадом 138 г"', log_line)
        self.assertIn("https://example.com/cookies/milka", log_line)

    def test_type_text_redacts_password_fields(self) -> None:
        before_state = _build_page_state(
            InteractiveElement(
                ref=7,
                tag="input",
                role="",
                text="",
                aria_label="Password",
                placeholder="Password",
                href="",
                name="",
                input_type="password",
                value="",
                disabled=False,
            )
        )

        formatted_args = format_tool_arguments(
            "type_text",
            {"ref": 7, "text": "super-secret-password"},
            before_state,
        )

        self.assertIn('"text": "[REDACTED]"', formatted_args)
        self.assertNotIn("super-secret-password", formatted_args)

    def test_result_log_omits_page_state_payload_and_shows_transition(self) -> None:
        before_state = _build_page_state(url="https://example.com/catalog")
        after_state = _build_page_state(url="https://example.com/product/42")
        result_log = build_step_result_log(
            step=5,
            fn_name="click",
            result={
                "success": True,
                "description": "Clicked element [120]",
                "page_state": "very large DOM dump",
                "screenshot_b64": "abc123",
            },
            before_state=before_state,
            after_state=after_state,
        )

        self.assertIn("Step 5 result", result_log)
        self.assertIn('Result: {"description": "Clicked element [120]", "success": true}', result_log)
        self.assertIn("Transition: https://example.com/catalog -> https://example.com/product/42", result_log)
        self.assertNotIn("very large DOM dump", result_log)
        self.assertNotIn("abc123", result_log)


if __name__ == "__main__":
    unittest.main()
