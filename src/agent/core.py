from __future__ import annotations

from playwright.async_api import BrowserContext, Page

from src.agent.graph import run_agent_graph


async def run_agent(task: str, page: Page, context: BrowserContext) -> str:
    return await run_agent_graph(task, page, context)
