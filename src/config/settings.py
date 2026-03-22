from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MAIN_MODEL = os.getenv("BROWSER_AGENT_MAIN_MODEL", "gpt-4o")
VISION_MODEL = os.getenv("BROWSER_AGENT_VISION_MODEL", "gpt-4o")
PLAN_MODEL = os.getenv("BROWSER_AGENT_PLAN_MODEL", "gpt-4o")
SUMMARY_MODEL = os.getenv("BROWSER_AGENT_SUMMARY_MODEL", "gpt-4o-mini")
TEMPERATURE = _float_env("BROWSER_AGENT_TEMPERATURE", 0.1)

# Browser
BROWSER_DATA_DIR = os.getenv("BROWSER_AGENT_BROWSER_DATA_DIR", ".browser-data")
VIEWPORT_WIDTH = _int_env("BROWSER_AGENT_VIEWPORT_WIDTH", 1280)
VIEWPORT_HEIGHT = _int_env("BROWSER_AGENT_VIEWPORT_HEIGHT", 900)
ACTION_DELAY_MS = _int_env("BROWSER_AGENT_ACTION_DELAY_MS", 2000)
NAVIGATION_TIMEOUT_MS = _int_env("BROWSER_AGENT_NAVIGATION_TIMEOUT_MS", 30000)

# Agent
MAX_STEPS = _int_env("BROWSER_AGENT_MAX_STEPS", 50)
MAX_RETRIES_PER_STEP = _int_env("BROWSER_AGENT_MAX_RETRIES_PER_STEP", 3)
ACTION_HISTORY_WINDOW = _int_env("BROWSER_AGENT_ACTION_HISTORY_WINDOW", 10)
STEPS_BEFORE_AUTO_REPLAN = _int_env("BROWSER_AGENT_STEPS_BEFORE_AUTO_REPLAN", 7)

# Memory
MAX_MEMORY_ENTRIES = _int_env("BROWSER_AGENT_MAX_MEMORY_ENTRIES", 30)
MAX_MEMORY_VALUE_LENGTH = _int_env("BROWSER_AGENT_MAX_MEMORY_VALUE_LENGTH", 500)

# Screenshots
SCREENSHOT_QUALITY = _int_env("BROWSER_AGENT_SCREENSHOT_QUALITY", 75)


def get_openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY")
