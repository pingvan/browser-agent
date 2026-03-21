import io
import logging
import re
import unittest

from src.utils.logger import _ColorFormatter, _SafeStreamHandler

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _BrokenStream:
    def write(self, _: str) -> int:
        raise ValueError("stream closed")

    def flush(self) -> None:
        raise ValueError("stream closed")


class LoggerTests(unittest.TestCase):
    def test_formatter_indents_multiline_messages(self) -> None:
        formatter = _ColorFormatter()
        record = logging.LogRecord(
            name="agent",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Step 1\nModel note: inspect page\nTool: get_page_state({})",
            args=(),
            exc_info=None,
        )

        formatted = _ANSI_RE.sub("", formatter.format(record))

        self.assertIn("[INFO] Step 1", formatted)
        self.assertIn("\n       Model note: inspect page", formatted)
        self.assertIn("\n       Tool: get_page_state({})", formatted)

    def test_safe_stream_handler_swallows_stream_errors(self) -> None:
        handler = _SafeStreamHandler(stream=_BrokenStream())
        handler.setFormatter(_ColorFormatter())
        record = logging.LogRecord(
            name="agent",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="line",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

    def test_safe_stream_handler_writes_to_healthy_stream(self) -> None:
        stream = io.StringIO()
        handler = _SafeStreamHandler(stream=stream)
        handler.setFormatter(_ColorFormatter())
        record = logging.LogRecord(
            name="agent",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="line one\nline two",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        output = _ANSI_RE.sub("", stream.getvalue())
        self.assertIn("[INFO] line one", output)
        self.assertIn("\n       line two", output)


if __name__ == "__main__":
    unittest.main()
