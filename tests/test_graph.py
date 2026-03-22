import unittest
from types import SimpleNamespace
from typing import Any, cast

from src.agent.graph import AgentRuntime, build_agent_graph
from src.agent.state import create_initial_state


class _DummyBrowser:
    async def observe(self) -> SimpleNamespace:
        return SimpleNamespace(
            page_state=SimpleNamespace(url="about:blank", title="Blank"),
            screenshot_b64="",
            elements=[],
        )


class _DummyPlanner:
    async def build_plan(self, **kwargs) -> tuple[list[dict[str, object]], str]:
        return ([{"id": 0, "description": "step", "status": "active", "result": ""}], "reason")


class _DummyAnalyzer:
    async def analyze_page(self, **kwargs) -> str:
        return "summary"


class _DummySecurity:
    def is_dangerous(self, *args, **kwargs) -> bool:
        return False

    async def request_confirmation(self, *args, **kwargs) -> bool:
        return True


class GraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_applies_state_updates_and_reaches_done_without_client(self) -> None:
        runtime = AgentRuntime(
            browser=cast(Any, _DummyBrowser()),
            planner=cast(Any, _DummyPlanner()),
            page_analyzer=cast(Any, _DummyAnalyzer()),
            security_layer=cast(Any, _DummySecurity()),
            client=None,
        )

        graph = build_agent_graph(runtime)
        result = await graph.ainvoke(create_initial_state("test task"))

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["step_count"], 1)
        self.assertTrue(result["final_report"])


if __name__ == "__main__":
    unittest.main()
