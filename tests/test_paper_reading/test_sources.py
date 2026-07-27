"""Paper source regression tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from handlers.paper_reading.pipeline.sources import ArxivSource


class _EmptyFeedResponse:
    text = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    def raise_for_status(self) -> None:
        return None


class _RecordingAsyncClient:
    created_with: list[dict[str, object]] = []
    requested_url = ""

    def __init__(self, **kwargs: object) -> None:
        self.created_with.append(kwargs)

    async def __aenter__(self) -> "_RecordingAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _EmptyFeedResponse:
        self.requested_url = url
        return _EmptyFeedResponse()


class ArxivSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_https_and_follows_redirects(self) -> None:
        _RecordingAsyncClient.created_with.clear()

        with patch(
            "handlers.paper_reading.pipeline.sources.httpx.AsyncClient",
            _RecordingAsyncClient,
        ):
            result = await ArxivSource().search("test", max_results=1)

        self.assertEqual(result, [])
        self.assertTrue(ArxivSource.API_URL.startswith("https://"))
        self.assertEqual(
            _RecordingAsyncClient.created_with,
            [{"timeout": 30.0, "follow_redirects": True}],
        )


if __name__ == "__main__":
    unittest.main()
