import logging
import os
import sys

import colorama
from colorama import Fore, Style

colorama.init(autoreset=True)
logging.raiseExceptions = False

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
        plain_tag = f"[{record.levelname}]"
        level_tag = f"{color}{plain_tag}{Style.RESET_ALL}"

        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"

        lines = message.splitlines() or [""]
        indent = " " * (len(plain_tag) + 1)
        formatted = f"{level_tag} {lines[0]}"
        for line in lines[1:]:
            formatted += f"\n{indent}{line}"
        return formatted


class _SafeStreamHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, OSError, ValueError)):
            return
        super().handleError(record)


def _make_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    if log.handlers:
        return log
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log.setLevel(getattr(logging, level_name, logging.INFO))
    handler = _SafeStreamHandler(stream=sys.stdout)
    handler.setFormatter(_ColorFormatter())
    log.addHandler(handler)
    log.propagate = False
    return log


logger = _make_logger()
