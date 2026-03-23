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


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MAIN_MODEL = os.getenv("BROWSER_AGENT_MAIN_MODEL", "gpt-4o")
VISION_MODEL = os.getenv("BROWSER_AGENT_VISION_MODEL", "gpt-4o")
SUMMARY_MODEL = os.getenv("BROWSER_AGENT_SUMMARY_MODEL", "gpt-4o-mini")
SECURITY_CLASSIFIER_MODEL = os.getenv("BROWSER_AGENT_SECURITY_MODEL", "gpt-4.1-nano")
SECURITY_CLASSIFIER_USE_SCREENSHOT = _bool_env(
    "BROWSER_AGENT_SECURITY_USE_SCREENSHOT", False
)
TEMPERATURE = _float_env("BROWSER_AGENT_TEMPERATURE", 0.1)

# Per-token pricing (USD) for cost tracking.
# Values: {"prompt": cost_per_token, "completion": cost_per_token}
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":       {"prompt": 2.5e-6,  "completion": 10.0e-6},
    "gpt-4o-mini":  {"prompt": 0.15e-6, "completion": 0.6e-6},
    "gpt-4.1":      {"prompt": 2.0e-6,  "completion": 8.0e-6},
    "gpt-4.1-mini": {"prompt": 0.4e-6,  "completion": 1.6e-6},
    "gpt-4.1-nano": {"prompt": 0.1e-6,  "completion": 0.4e-6},
}

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
MAX_STUCK_STEPS = _int_env("BROWSER_AGENT_MAX_STUCK_STEPS", 4)
PAGE_STATE_CHAR_BUDGET = _int_env("BROWSER_AGENT_PAGE_STATE_CHAR_BUDGET", 2400)
RAW_PAGE_TEXT_CHAR_BUDGET = _int_env("BROWSER_AGENT_RAW_PAGE_TEXT_CHAR_BUDGET", 1200)
MAX_PROMPT_ELEMENTS = _int_env("BROWSER_AGENT_MAX_PROMPT_ELEMENTS", 40)

# Memory
MAX_MEMORY_ENTRIES = _int_env("BROWSER_AGENT_MAX_MEMORY_ENTRIES", 30)
MAX_MEMORY_VALUE_LENGTH = _int_env("BROWSER_AGENT_MAX_MEMORY_VALUE_LENGTH", 500)

# Screenshots
SCREENSHOT_QUALITY = _int_env("BROWSER_AGENT_SCREENSHOT_QUALITY", 75)


def get_openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY")
