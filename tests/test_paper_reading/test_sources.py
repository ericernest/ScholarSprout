"""Paper source regression tests."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from handlers.paper_reading.pipeline.metadata import PaperMetadata
from handlers.paper_reading.pipeline.sources import (
    ArxivSource,
    OpenAlexFallbackSource,
    PaperPipeline,
    PaperRetrievalError,
    SemanticScholarSource,
    SourceTemporarilyUnavailable,
    normalize_arxiv_id,
)


class _EmptyFeedResponse:
    text = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    status_code = 200
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None


class _RecordingAsyncClient:
    created_with: list[dict[str, object]] = []
    requested_url = ""
    requested_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        self.created_with.append(kwargs)

    async def __aenter__(self) -> "_RecordingAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _EmptyFeedResponse:
        _RecordingAsyncClient.requested_url = url
        _RecordingAsyncClient.requested_kwargs = kwargs
        return _EmptyFeedResponse()


class ArxivSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_https_and_follows_redirects(self) -> None:
        _RecordingAsyncClient.created_with.clear()

        with (
            patch(
                "handlers.paper_reading.pipeline.sources.httpx.AsyncClient",
                _RecordingAsyncClient,
            ),
            patch(
                "handlers.paper_reading.pipeline.sources._wait_for_arxiv_slot",
                new=AsyncMock(),
            ),
        ):
            result = await ArxivSource().search("test", max_results=1)

        self.assertEqual(result, [])
        self.assertTrue(ArxivSource.API_URL.startswith("https://"))
        self.assertEqual(len(_RecordingAsyncClient.created_with), 1)
        client_options = _RecordingAsyncClient.created_with[0]
        self.assertEqual(client_options["timeout"], 30.0)
        self.assertTrue(client_options["follow_redirects"])
        self.assertIn("User-Agent", client_options["headers"])

    async def test_exact_id_uses_id_list_instead_of_full_text_search(self) -> None:
        with (
            patch(
                "handlers.paper_reading.pipeline.sources.httpx.AsyncClient",
                _RecordingAsyncClient,
            ),
            patch(
                "handlers.paper_reading.pipeline.sources._wait_for_arxiv_slot",
                new=AsyncMock(),
            ),
        ):
            await ArxivSource().search("arxiv:2506.07398", max_results=3)

        params = _RecordingAsyncClient.requested_kwargs["params"]
        self.assertEqual(params["id_list"], "2506.07398")
        self.assertNotIn("search_query", params)

    async def test_429_honors_retry_after_then_succeeds(self) -> None:
        request = httpx.Request("GET", ArxivSource.API_URL)
        limited = httpx.Response(
            429,
            headers={"Retry-After": "7"},
            request=request,
        )
        success = httpx.Response(
            200,
            text=_EmptyFeedResponse.text,
            request=request,
        )
        responses = [limited, success]

        class _SequenceAsyncClient(_RecordingAsyncClient):
            async def get(self, url: str, **kwargs: object) -> httpx.Response:
                return responses.pop(0)

        sleep = AsyncMock()
        with (
            patch(
                "handlers.paper_reading.pipeline.sources.httpx.AsyncClient",
                _SequenceAsyncClient,
            ),
            patch(
                "handlers.paper_reading.pipeline.sources._wait_for_arxiv_slot",
                new=AsyncMock(),
            ),
            patch(
                "handlers.paper_reading.pipeline.sources.asyncio.sleep",
                new=sleep,
            ),
        ):
            result = await ArxivSource().search("rate limit test", max_results=1)

        self.assertEqual(result, [])
        sleep.assert_awaited_once_with(7.0)


class PaperPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_normalizes_supported_arxiv_id_forms(self) -> None:
        self.assertEqual(normalize_arxiv_id("2506.07398"), "2506.07398")
        self.assertEqual(normalize_arxiv_id("arxiv:2506.07398v2"), "2506.07398")
        self.assertEqual(
            normalize_arxiv_id("https://arxiv.org/abs/2506.07398"),
            "2506.07398",
        )
        self.assertIsNone(normalize_arxiv_id("attention mechanism"))

    async def test_exact_id_falls_back_to_semantic_scholar(self) -> None:
        class _NotFoundResolver:
            async def fetch_by_arxiv_id(self, arxiv_id: str) -> None:
                return None

        class _FailedArxiv:
            async def fetch_by_id(self, source_id: str) -> None:
                raise httpx.HTTPError("limited")

        class _SemanticScholar:
            async def fetch_by_id(self, source_id: str) -> PaperMetadata:
                self.source_id = source_id
                return PaperMetadata(
                    paper_id="paper-1",
                    source="semantic_scholar",
                    source_id="s2-1",
                    arxiv_id="2506.07398",
                    title="Recovered",
                )

        pipeline = PaperPipeline()
        semantic = _SemanticScholar()
        pipeline.openalex_fallback = _NotFoundResolver()
        pipeline.sources = {
            "arxiv": _FailedArxiv(),
            "semantic_scholar": semantic,
        }

        papers = await pipeline.search("2506.07398", max_results=1)

        self.assertEqual([paper.title for paper in papers], ["Recovered"])
        self.assertEqual(semantic.source_id, "ARXIV:2506.07398")

    def test_openalex_fallback_restores_exact_metadata(self) -> None:
        paper = OpenAlexFallbackSource._parse_paper(
            {
                "id": "https://openalex.org/W123",
                "title": "Recovered metadata",
                "publication_year": 2025,
                "publication_date": "2025-06-09",
                "doi": "https://doi.org/10.1000/test",
                "cited_by_count": 4,
                "authorships": [
                    {"author": {"display_name": "Ada Researcher"}},
                ],
                "primary_location": {
                    "source": {"display_name": "arXiv"},
                },
                "abstract_inverted_index": {
                    "Hello": [0],
                    "world": [1],
                },
            },
            "2506.07398",
        )

        self.assertEqual(paper.source, "openalex")
        self.assertEqual(paper.source_id, "https://openalex.org/W123")
        self.assertEqual(paper.arxiv_id, "2506.07398")
        self.assertEqual(paper.abstract, "Hello world")
        self.assertEqual([author.name for author in paper.authors], ["Ada Researcher"])
        self.assertEqual(paper.doi, "10.1000/test")
        self.assertEqual(paper.pdf_url, "https://arxiv.org/pdf/2506.07398")

    async def test_all_provider_failures_are_not_reported_as_empty_success(self) -> None:
        class _FailedSource:
            async def search(self, query: str, max_results: int) -> list[PaperMetadata]:
                raise httpx.HTTPError("limited")

        pipeline = PaperPipeline()
        pipeline.openalex_fallback = _FailedSource()
        pipeline.sources = {
            "arxiv": _FailedSource(),
            "semantic_scholar": _FailedSource(),
        }

        with self.assertRaises(PaperRetrievalError) as raised:
            await pipeline.search("unique all-provider-failure query")

        self.assertCountEqual(
            raised.exception.errors,
            ["arxiv", "semantic_scholar", "openalex"],
        )

    async def test_keyword_search_uses_openalex_when_both_primary_sources_fail(
        self,
    ) -> None:
        class _FailedSource:
            async def search(self, query: str, max_results: int) -> list[PaperMetadata]:
                raise httpx.HTTPError("limited")

        class _OpenAlexFallback:
            async def search(
                self,
                query: str,
                max_results: int,
            ) -> list[PaperMetadata]:
                return [
                    PaperMetadata(
                        paper_id="openalex-1",
                        source="openalex",
                        source_id="https://openalex.org/W1",
                        title="Fallback result",
                    )
                ]

        pipeline = PaperPipeline()
        pipeline.sources = {
            "arxiv": _FailedSource(),
            "semantic_scholar": _FailedSource(),
        }
        pipeline.openalex_fallback = _OpenAlexFallback()

        papers = await pipeline.search("unique keyword fallback query")

        self.assertEqual([paper.title for paper in papers], ["Fallback result"])
        self.assertEqual(papers[0].source, "openalex")

    async def test_keyword_search_uses_openalex_when_primary_results_are_empty(
        self,
    ) -> None:
        class _EmptySource:
            async def search(
                self,
                query: str,
                max_results: int,
            ) -> list[PaperMetadata]:
                return []

        class _TemporarilyUnavailableSource:
            async def search(
                self,
                query: str,
                max_results: int,
            ) -> list[PaperMetadata]:
                raise SourceTemporarilyUnavailable("HTTP 429 cooldown")

        class _OpenAlexFallback:
            async def search(
                self,
                query: str,
                max_results: int,
            ) -> list[PaperMetadata]:
                return [
                    PaperMetadata(
                        paper_id="openalex-empty-primary",
                        source="openalex",
                        source_id="https://openalex.org/W2",
                        title="Recovered from empty primary results",
                    )
                ]

        pipeline = PaperPipeline()
        pipeline.sources = {
            "arxiv": _EmptySource(),
            "semantic_scholar": _TemporarilyUnavailableSource(),
        }
        pipeline.openalex_fallback = _OpenAlexFallback()

        papers = await pipeline.search("unique empty primary query")

        self.assertEqual(
            [paper.title for paper in papers],
            ["Recovered from empty primary results"],
        )


class SemanticScholarSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import handlers.paper_reading.pipeline.sources as sources_module

        sources_module._SEMANTIC_SCHOLAR_RATE_LIMITED_UNTIL = 0.0

    async def asyncTearDown(self) -> None:
        import handlers.paper_reading.pipeline.sources as sources_module

        sources_module._SEMANTIC_SCHOLAR_RATE_LIMITED_UNTIL = 0.0

    async def test_public_api_429_starts_cooldown_without_retries(self) -> None:
        request = httpx.Request("GET", SemanticScholarSource.API_URL)
        limited = httpx.Response(429, request=request)

        class _RateLimitedAsyncClient(_RecordingAsyncClient):
            request_count = 0

            async def get(self, url: str, **kwargs: object) -> httpx.Response:
                self.__class__.request_count += 1
                return limited

        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "handlers.paper_reading.pipeline.sources.httpx.AsyncClient",
                _RateLimitedAsyncClient,
            ),
        ):
            with self.assertRaises(SourceTemporarilyUnavailable):
                await SemanticScholarSource().search("rate limited query")
            with self.assertRaises(SourceTemporarilyUnavailable):
                await SemanticScholarSource().search("second query")

        self.assertEqual(_RateLimitedAsyncClient.request_count, 1)

    def test_empty_exception_has_visible_description(self) -> None:
        from handlers.paper_reading.pipeline.sources import _describe_error

        self.assertEqual(_describe_error(httpx.ReadTimeout("")), "ReadTimeout")


if __name__ == "__main__":
    unittest.main()
