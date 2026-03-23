import os
import unittest
from unittest.mock import patch

from src.config.settings import get_openai_api_key


class SettingsTests(unittest.TestCase):
    def test_get_openai_api_key_reads_runtime_env(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            self.assertEqual(get_openai_api_key(), "sk-test")


if __name__ == "__main__":
    unittest.main()
