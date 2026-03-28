from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Settings
from app.min_chat import ensure_llm_configured


class MinimalChatTests(unittest.TestCase):
    def test_ensure_llm_configured_raise_error_when_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings(
                llm_api_key="",
                llm_base_url="",
                llm_model_id="",
            )
            with self.assertRaises(ValueError):
                ensure_llm_configured(settings)

    def test_ensure_llm_configured_pass_when_present(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LLM_API_KEY": "test-key",
                "LLM_BASE_URL": "https://example.com/v1",
                "LLM_MODEL_ID": "gpt-5-mini",
            },
            clear=True,
        ):
            settings = Settings(
                llm_api_key="test-key",
                llm_base_url="https://example.com/v1",
                llm_model_id="gpt-5-mini",
            )
            ensure_llm_configured(settings)


if __name__ == "__main__":
    unittest.main()
