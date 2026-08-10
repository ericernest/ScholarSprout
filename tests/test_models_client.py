from __future__ import annotations

import unittest

from config.schema import OpenAIClientConfig
from models.client import OpenAIClient


class OpenAIClientRequestTests(unittest.TestCase):
    @staticmethod
    def _client(model_name: str) -> OpenAIClient:
        client = object.__new__(OpenAIClient)
        client.config = OpenAIClientConfig(model_name=model_name)
        return client

    def test_structured_deepseek_v4_call_can_disable_thinking(self) -> None:
        kwargs = self._client("deepseek-v4-flash")._chat_kwargs(
            messages=[{"role": "user", "content": "return json"}],
            tools=None,
            tool_choice=None,
            max_tokens=4000,
            response_format={"type": "json_object"},
            model_name=None,
            disable_thinking=True,
        )

        self.assertEqual(
            kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )

    def test_non_deepseek_model_does_not_receive_provider_specific_option(self) -> None:
        kwargs = self._client("qwen-chat")._chat_kwargs(
            messages=[{"role": "user", "content": "return json"}],
            tools=None,
            tool_choice=None,
            max_tokens=4000,
            response_format={"type": "json_object"},
            model_name=None,
            disable_thinking=True,
        )

        self.assertNotIn("extra_body", kwargs)


if __name__ == "__main__":
    unittest.main()
