import logging
import os

import colorama
from colorama import Fore, Style

colorama.init(autoreset=True)

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: Fore.CYAN,
    logging.INFO: Fore.GREEN,
    logging.WARNING: Fore.YELLOW,
    logging.ERROR: Fore.RED,
    logging.CRITICAL: Fore.RED + Style.BRIGHT,
}


class _ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, "")
        level_tag = f"{color}[{record.levelname}]{Style.RESET_ALL}"
        return f"{level_tag} {record.getMessage()}"


def _make_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    if log.handlers:
        return log
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log.setLevel(getattr(logging, level_name, logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(_ColorFormatter())
    log.addHandler(handler)
    log.propagate = False
    return log


logger = _make_logger()
