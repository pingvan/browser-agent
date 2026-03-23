from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

__all__ = ["run_agent"]


async def run_agent(task: str, page: Page, context: BrowserContext) -> str:
    from src.agent.core import run_agent as _run_agent

    return await _run_agent(task=task, page=page, context=context)
