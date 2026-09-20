"""多源论文检索 — arXiv, Semantic Scholar, OpenAlex 降级。

设计:
- 统一 PaperSource 基类接口
- ArxivSource: 通过 arXiv API (Atom XML) 检索
- SemanticScholarSource: 通过 Semantic Scholar API (JSON) 检索
- OpenAlexFallbackSource: 快速解析 arXiv ID，并在两个主源失败时接管关键词搜索
- PaperPipeline: 统一入口，支持多源并行搜索和去重
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic
from typing import Any
from uuid import uuid4
from pathlib import Path
import feedparser
import httpx

from handlers.paper_reading.pipeline.metadata import Author, PaperMetadata
from handlers.paper_reading.pipeline.parser import PDFParser

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_ARXIV_MIN_INTERVAL_SECONDS = 3.0
_ARXIV_RATE_LOCK = Lock()
_ARXIV_NEXT_REQUEST_AT = 0.0
_SEMANTIC_SCHOLAR_COOLDOWN_SECONDS = 60.0
_SEMANTIC_SCHOLAR_RATE_LOCK = Lock()
_SEMANTIC_SCHOLAR_RATE_LIMITED_UNTIL = 0.0
_SEARCH_CACHE_TTL_SECONDS = 3600.0
_SEARCH_CACHE_MAX_ENTRIES = 128
_SEARCH_CACHE_LOCK = Lock()
_SEARCH_CACHE: OrderedDict[
    str, tuple[float, list[PaperMetadata]]
] = OrderedDict()


class PaperRetrievalError(RuntimeError):
    """所有目标论文源均请求失败。"""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        details = "; ".join(f"{source}: {error}" for source, error in errors.items())
        super().__init__(f"论文源请求失败（{details}）")


class SourceTemporarilyUnavailable(RuntimeError):
    """论文源暂时不可用，但其他来源仍可继续接管。"""


def _describe_error(error: BaseException) -> str:
    """确保日志和接口错误中不会出现空异常文本。"""
    message = str(error).strip()
    return message or error.__class__.__name__


def normalize_arxiv_id(value: str) -> str | None:
    """从裸 ID、arxiv: 前缀或 abs/pdf URL 中提取 arXiv ID。"""
    candidate = value.strip()
    candidate = re.sub(
        r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\.pdf$", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"^arxiv:\s*", "", candidate, flags=re.IGNORECASE)
    if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", candidate):
        return re.sub(r"v\d+$", "", candidate)
    if re.fullmatch(r"[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?", candidate, re.IGNORECASE):
        return re.sub(r"v\d+$", "", candidate)
    return None


def _retry_after_seconds(response: Any, attempt: int, base_delay: float) -> float:
    """读取 Retry-After；无有效值时使用指数退避。"""
    delay = base_delay * (2 ** (attempt - 1))
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("Retry-After")
    if value:
        try:
            delay = max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(str(value))
                if parsed.tzinfo is None:
                    parsed = parsed.astimezone()
                delay = max(
                    0.0,
                    (parsed - datetime.now(parsed.tzinfo)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return min(delay, 60.0)


def _semantic_scholar_cooldown_remaining() -> float:
    with _SEMANTIC_SCHOLAR_RATE_LOCK:
        return max(0.0, _SEMANTIC_SCHOLAR_RATE_LIMITED_UNTIL - monotonic())


def _start_semantic_scholar_cooldown(response: Any) -> float:
    """公共 API 返回 429 后暂停请求，避免同一 Agent 回合继续撞限流。"""
    global _SEMANTIC_SCHOLAR_RATE_LIMITED_UNTIL
    delay = max(
        _SEMANTIC_SCHOLAR_COOLDOWN_SECONDS,
        _retry_after_seconds(response, attempt=1, base_delay=1.0),
    )
    with _SEMANTIC_SCHOLAR_RATE_LOCK:
        _SEMANTIC_SCHOLAR_RATE_LIMITED_UNTIL = max(
            _SEMANTIC_SCHOLAR_RATE_LIMITED_UNTIL,
            monotonic() + delay,
        )
    return delay


async def _wait_for_arxiv_slot() -> None:
    """为全进程 arXiv 请求预留至少三秒间隔。"""
    global _ARXIV_NEXT_REQUEST_AT
    with _ARXIV_RATE_LOCK:
        now = monotonic()
        request_at = max(now, _ARXIV_NEXT_REQUEST_AT)
        _ARXIV_NEXT_REQUEST_AT = request_at + _ARXIV_MIN_INTERVAL_SECONDS
    delay = request_at - now
    if delay > 0:
        await asyncio.sleep(delay)


def _cache_get(key: str) -> list[PaperMetadata] | None:
    with _SEARCH_CACHE_LOCK:
        item = _SEARCH_CACHE.get(key)
        if item is None:
            return None
        expires_at, papers = item
        if expires_at <= monotonic():
            _SEARCH_CACHE.pop(key, None)
            return None
        _SEARCH_CACHE.move_to_end(key)
        return deepcopy(papers)


def _cache_set(key: str, papers: list[PaperMetadata]) -> None:
    if not papers:
        return
    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[key] = (
            monotonic() + _SEARCH_CACHE_TTL_SECONDS,
            deepcopy(papers),
        )
        _SEARCH_CACHE.move_to_end(key)
        while len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX_ENTRIES:
            _SEARCH_CACHE.popitem(last=False)


def _parse_date(date_str: str) -> date | None:
    """解析多种日期格式。"""
    if not date_str:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(date_str, fmt).date()
        except (ValueError, TypeError):
            continue
    # 尝试 ISO 格式
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


def _parse_year(year_val: int | None) -> date | None:
    """将年份整数转为 date。"""
    if year_val is None:
        return None
    return date(year_val, 1, 1)


# ── 基类 ──

class PaperSource:
    """多源论文检索基类。"""

    SOURCE_NAME: str = "unknown"

    async def search(self, query: str, max_results: int = 10) -> list[PaperMetadata]:
        raise NotImplementedError

    async def fetch_by_id(self, source_id: str) -> PaperMetadata | None:
        raise NotImplementedError


# ── arXiv ──

class ArxivSource(PaperSource):
    """arXiv API 检索。

    API: https://export.arxiv.org/api/query?search_query=all:{query}&max_results={n}
    返回: Atom XML，通过 feedparser 解析
    """

    SOURCE_NAME = "arxiv"
    API_URL = "https://export.arxiv.org/api/query"
    MAX_ATTEMPTS = 3

    async def search(self, query: str, max_results: int = 10) -> list[PaperMetadata]:
        """通过 arXiv API 检索论文。"""
        exact_id = normalize_arxiv_id(query)
        params = {
            **(
                {"id_list": exact_id}
                if exact_id
                else {
                    "search_query": f"all:{query}",
                    "start": 0,
                    "sortBy": "relevance",
                }
            ),
            "max_results": min(max_results, 50),
        }
        response = await self._request(params, operation="search")
        return self._parse_feed(response.text)

    async def fetch_by_id(self, source_id: str) -> PaperMetadata | None:
        """通过 arXiv ID 获取单篇论文。"""
        arxiv_id = normalize_arxiv_id(source_id)
        if arxiv_id is None:
            raise ValueError(f"无效的 arXiv ID: {source_id}")
        params = {
            "id_list": arxiv_id,
            "max_results": 1,
        }
        response = await self._request(params, operation="fetch_by_id")
        papers = self._parse_feed(response.text)
        return papers[0] if papers else None

    async def _request(self, params: dict[str, Any], *, operation: str) -> Any:
        headers = {
            "User-Agent": "ScholarSprout/0.1 paper-reading",
            "Accept": "application/atom+xml",
        }
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            max_attempts = 1 if operation == "fetch_by_id" else self.MAX_ATTEMPTS
            for attempt in range(1, max_attempts + 1):
                await _wait_for_arxiv_slot()
                try:
                    response = await client.get(self.API_URL, params=params)
                except httpx.TransportError as error:
                    if attempt >= max_attempts:
                        raise
                    delay = min(3.0 * (2 ** (attempt - 1)), 60.0)
                    logger.warning(
                        "arXiv %s attempt %d transport failure; retrying in %.1fs: %s",
                        operation,
                        attempt,
                        delay,
                        error,
                    )
                    await asyncio.sleep(delay)
                    continue

                status_code = getattr(response, "status_code", 200)
                if status_code in _RETRYABLE_STATUS_CODES:
                    if attempt >= max_attempts:
                        response.raise_for_status()
                    delay = max(
                        _ARXIV_MIN_INTERVAL_SECONDS,
                        _retry_after_seconds(response, attempt, 3.0),
                    )
                    logger.warning(
                        "arXiv %s attempt %d returned HTTP %s; retrying in %.1fs",
                        operation,
                        attempt,
                        status_code,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response

        raise RuntimeError("arXiv request loop ended without a response")

    @staticmethod
    def _parse_feed(xml_text: str) -> list[PaperMetadata]:
        feed = feedparser.parse(xml_text)
        papers: list[PaperMetadata] = []
        for entry in feed.entries:
            arxiv_id = re.sub(
                r"v\d+$",
                "",
                entry.get("id", "").split("/abs/")[-1],
            )
            authors = [
                Author(name=author.get("name", ""))
                for author in entry.get("authors", [])
            ]
            categories = [
                tag["term"]
                for tag in entry.get("tags", [])
                if tag.get("term")
            ]
            url = entry.get("id", "")
            papers.append(PaperMetadata(
                paper_id=str(uuid4()),
                source="arxiv",
                source_id=f"arxiv:{arxiv_id}",
                arxiv_id=arxiv_id,
                title=entry.get("title", "").strip().replace("\n", " "),
                authors=authors,
                abstract=entry.get("summary", "").strip().replace("\n", " "),
                published_date=_parse_date(entry.get("published", "")),
                updated_date=_parse_date(entry.get("updated", "")),
                categories=categories,
                doi=entry.get("arxiv_doi", "") or "",
                url=url,
                pdf_url=url.replace("/abs/", "/pdf/"),
                journal_ref=entry.get("arxiv_journal_ref", "") or "",
            ))
        return papers


# ── Semantic Scholar ──

class SemanticScholarSource(PaperSource):
    """Semantic Scholar API 检索。

    API: https://api.semanticscholar.org/graph/v1/paper/search
    返回: JSON（无需 XML 解析，更高效）
    """

    SOURCE_NAME = "semantic_scholar"
    API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper"
    MAX_ATTEMPTS = 3
    FIELDS = (
        "title,authors,abstract,year,externalIds,url,"
        "citationCount,venue,journal"
    )

    async def search(self, query: str, max_results: int = 10) -> list[PaperMetadata]:
        """通过 Semantic Scholar API 检索论文。"""
        exact_id = normalize_arxiv_id(query)
        if exact_id:
            paper = await self.fetch_by_id(f"ARXIV:{exact_id}")
            return [paper] if paper is not None else []

        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": self.FIELDS,
        }
        response = await self._request(self.API_URL, params, operation="search")
        return [
            self._parse_paper(item)
            for item in response.json().get("data", [])
        ]

    async def fetch_by_id(self, source_id: str) -> PaperMetadata | None:
        """通过 Semantic Scholar paper ID 获取详情。"""
        arxiv_id = normalize_arxiv_id(source_id)
        lookup_id = f"ARXIV:{arxiv_id}" if arxiv_id else source_id
        url = f"{self.PAPER_URL}/{lookup_id}"
        response = await self._request(
            url,
            {"fields": self.FIELDS},
            operation="fetch_by_id",
        )
        item = response.json()
        if not item:
            return None
        return self._parse_paper(item, source_id=lookup_id)

    async def _request(
        self,
        url: str,
        params: dict[str, Any],
        *,
        operation: str,
    ) -> Any:
        headers = {
            "User-Agent": "ScholarSprout/0.1 paper-reading",
            "Accept": "application/json",
        }
        api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        if api_key:
            headers["x-api-key"] = api_key
        else:
            cooldown = _semantic_scholar_cooldown_remaining()
            if cooldown > 0:
                raise SourceTemporarilyUnavailable(
                    "Semantic Scholar public API is cooling down after HTTP 429 "
                    f"({cooldown:.0f}s remaining)"
                )

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            max_attempts = 1 if operation == "fetch_by_id" else self.MAX_ATTEMPTS
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.get(url, params=params)
                except httpx.TransportError as error:
                    if attempt >= max_attempts:
                        raise
                    delay = min(1.0 * (2 ** (attempt - 1)), 60.0)
                    logger.warning(
                        "Semantic Scholar %s attempt %d transport failure; "
                        "retrying in %.1fs: %s",
                        operation,
                        attempt,
                        delay,
                        error,
                    )
                    await asyncio.sleep(delay)
                    continue

                status_code = getattr(response, "status_code", 200)
                if status_code == 429 and not api_key:
                    cooldown = _start_semantic_scholar_cooldown(response)
                    logger.info(
                        "Semantic Scholar public API returned HTTP 429; "
                        "using fallback sources for %.0fs",
                        cooldown,
                    )
                    raise SourceTemporarilyUnavailable(
                        "Semantic Scholar public API returned HTTP 429; "
                        f"fallback cooldown started for {cooldown:.0f}s"
                    )
                if status_code in _RETRYABLE_STATUS_CODES:
                    if attempt >= max_attempts:
                        response.raise_for_status()
                    delay = _retry_after_seconds(response, attempt, 1.0)
                    logger.warning(
                        "Semantic Scholar %s attempt %d returned HTTP %s; "
                        "retrying in %.1fs",
                        operation,
                        attempt,
                        status_code,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response

        raise RuntimeError("Semantic Scholar request loop ended without a response")

    @staticmethod
    def _parse_paper(
        item: dict[str, Any],
        *,
        source_id: str = "",
    ) -> PaperMetadata:
        authors = [Author(name=a.get("name", "")) for a in item.get("authors", [])]
        ext = item.get("externalIds", {}) or {}
        venue_raw = item.get("venue", "") or ""
        journal = item.get("journal", {}) or {}
        return PaperMetadata(
            paper_id=str(uuid4()),
            source="semantic_scholar",
            source_id=item.get("paperId", "") or source_id,
            title=item.get("title", ""),
            authors=authors,
            abstract=item.get("abstract", "") or "",
            year=item.get("year"),
            published_date=_parse_year(item.get("year")),
            citation_count=item.get("citationCount"),
            doi=ext.get("DOI", ""),
            arxiv_id=ext.get("ArXiv", ""),
            url=item.get("url", ""),
            venue=journal.get("name", "") or venue_raw,
        )


class OpenAlexFallbackSource:
    """按 arXiv 落地页精确解析，并提供关键词检索降级。"""

    API_URL = "https://api.openalex.org/works"
    MAX_ATTEMPTS = 3
    SELECT_FIELDS = (
        "id,title,authorships,publication_year,publication_date,doi,"
        "cited_by_count,primary_location,abstract_inverted_index"
    )

    async def fetch_by_arxiv_id(self, arxiv_id: str) -> PaperMetadata | None:
        params = {
            # OpenAlex 当前收录的 arXiv location 使用 http 规范值。
            "filter": (
                "locations.landing_page_url:"
                f"http://arxiv.org/abs/{arxiv_id}"
            ),
            "per-page": 1,
            "select": self.SELECT_FIELDS,
        }
        api_key = os.getenv("OPENALEX_API_KEY", "").strip()
        if api_key:
            params["api_key"] = api_key

        response = await self._request(params)
        results = response.json().get("results", [])
        if not results:
            return None
        return self._parse_paper(results[0], arxiv_id)

    async def search(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[PaperMetadata]:
        params = {
            "search": query,
            "per-page": min(max_results, 50),
            "select": self.SELECT_FIELDS,
        }
        api_key = os.getenv("OPENALEX_API_KEY", "").strip()
        if api_key:
            params["api_key"] = api_key
        response = await self._request(params, operation="search")
        return [
            self._parse_paper(item)
            for item in response.json().get("results", [])
        ]

    async def _request(
        self,
        params: dict[str, Any],
        *,
        operation: str = "exact lookup",
    ) -> Any:
        headers = {
            "User-Agent": "ScholarSprout/0.1 paper-reading",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                try:
                    response = await client.get(self.API_URL, params=params)
                except httpx.TransportError as error:
                    if attempt >= self.MAX_ATTEMPTS:
                        raise
                    delay = min(1.0 * (2 ** (attempt - 1)), 60.0)
                    logger.warning(
                        "OpenAlex %s attempt %d transport failure; "
                        "retrying in %.1fs: %s",
                        operation,
                        attempt,
                        delay,
                        error,
                    )
                    await asyncio.sleep(delay)
                    continue

                status_code = getattr(response, "status_code", 200)
                if status_code in _RETRYABLE_STATUS_CODES:
                    if attempt >= self.MAX_ATTEMPTS:
                        response.raise_for_status()
                    delay = _retry_after_seconds(response, attempt, 1.0)
                    logger.warning(
                        "OpenAlex %s attempt %d returned HTTP %s; "
                        "retrying in %.1fs",
                        operation,
                        attempt,
                        status_code,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response

        raise RuntimeError("OpenAlex request loop ended without a response")

    @classmethod
    def _parse_paper(
        cls,
        item: dict[str, Any],
        arxiv_id: str = "",
    ) -> PaperMetadata:
        primary_location = item.get("primary_location") or {}
        source = primary_location.get("source") or {}
        doi = str(item.get("doi") or "")
        doi = re.sub(r"^https?://doi\.org/", "", doi, flags=re.IGNORECASE)
        url = str(primary_location.get("landing_page_url") or item.get("id") or "")
        pdf_url = str(primary_location.get("pdf_url") or "")
        if arxiv_id:
            url = f"https://arxiv.org/abs/{arxiv_id}"
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        return PaperMetadata(
            paper_id=str(uuid4()),
            source="openalex",
            source_id=str(item.get("id") or ""),
            arxiv_id=arxiv_id,
            title=str(item.get("title") or ""),
            authors=[
                Author(name=str((authorship.get("author") or {}).get("display_name") or ""))
                for authorship in item.get("authorships", [])
                if str((authorship.get("author") or {}).get("display_name") or "")
            ],
            abstract=cls._restore_abstract(item.get("abstract_inverted_index")),
            year=item.get("publication_year"),
            published_date=_parse_date(str(item.get("publication_date") or "")),
            citation_count=item.get("cited_by_count"),
            doi=doi,
            url=url,
            pdf_url=pdf_url,
            venue=str(source.get("display_name") or ""),
        )

    @staticmethod
    def _restore_abstract(index: Any) -> str:
        if not isinstance(index, dict) or not index:
            return ""
        positions = [
            position
            for raw_positions in index.values()
            if isinstance(raw_positions, list)
            for position in raw_positions
            if isinstance(position, int) and position >= 0
        ]
        if not positions:
            return ""
        words = [""] * (max(positions) + 1)
        for word, raw_positions in index.items():
            if not isinstance(raw_positions, list):
                continue
            for position in raw_positions:
                if isinstance(position, int) and 0 <= position < len(words):
                    words[position] = str(word)
        return " ".join(word for word in words if word)


# ── 统一流水线入口 ──

class PaperPipeline:
    """论文获取流水线统一入口。

    用法:
        pipeline = PaperPipeline()
        papers = await pipeline.search("attention mechanism")
        metadata = pipeline.parse_pdf(Path("paper.pdf"))
    """

    def __init__(self) -> None:
        self.sources: dict[str, PaperSource] = {
            "arxiv": ArxivSource(),
            "semantic_scholar": SemanticScholarSource(),
        }
        self.openalex_fallback = OpenAlexFallbackSource()
        self.parser = PDFParser()

    async def search(
        self,
        query: str,
        sources: list[str] | None = None,
        max_results: int = 10,
    ) -> list[PaperMetadata]:
        """多源并行搜索，自动去重。

        Args:
            query: 搜索关键词
            sources: 指定搜索源（None=全部）
            max_results: 每个源的最大结果数

        Returns:
            去重后的 PaperMetadata 列表
        """
        target_sources = [
            name
            for name in (sources or list(self.sources.keys()))
            if name in self.sources
        ]
        if not target_sources:
            raise ValueError("没有可用的论文搜索源")

        normalized_query = query.strip()
        cache_key = "|".join(
            [
                ",".join(target_sources),
                normalized_query.casefold(),
                str(max_results),
            ]
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        exact_id = normalize_arxiv_id(normalized_query)
        if exact_id:
            papers = await self._fetch_exact_arxiv_id(exact_id, target_sources)
            _cache_set(cache_key, papers)
            return papers

        tasks = [
            self.sources[name].search(normalized_query, max_results)
            for name in target_sources
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers: list[PaperMetadata] = []
        errors: dict[str, str] = {}
        successful_sources = 0
        for name, result in zip(target_sources, results_list):
            if isinstance(result, Exception):
                error_message = _describe_error(result)
                errors[name] = error_message
                if isinstance(result, SourceTemporarilyUnavailable):
                    logger.info(
                        "Source %s temporarily unavailable; continuing with "
                        "fallback sources: %s",
                        name,
                        error_message,
                    )
                else:
                    logger.warning("Source %s failed: %s", name, error_message)
            elif isinstance(result, list):
                successful_sources += 1
                all_papers.extend(result)

        if not all_papers:
            try:
                fallback_results = await self.openalex_fallback.search(
                    normalized_query,
                    max_results,
                )
            except Exception as error:
                errors["openalex"] = _describe_error(error)
                if successful_sources == 0 and errors:
                    raise PaperRetrievalError(errors) from error
                logger.warning(
                    "OpenAlex fallback failed after empty primary results: %s",
                    errors["openalex"],
                )
            else:
                papers = self._deduplicate(fallback_results)
                _cache_set(cache_key, papers)
                return papers

        papers = self._deduplicate(all_papers)
        _cache_set(cache_key, papers)
        return papers

    async def _fetch_exact_arxiv_id(
        self,
        arxiv_id: str,
        target_sources: list[str],
    ) -> list[PaperMetadata]:
        """精确 ID 优先走快速解析器，未收录时再查询两个主源。"""
        errors: dict[str, str] = {}
        successful_sources = 0

        async def resolve_openalex() -> PaperMetadata | None:
            nonlocal successful_sources
            try:
                paper = await self.openalex_fallback.fetch_by_arxiv_id(arxiv_id)
                successful_sources += 1
                return paper
            except Exception as error:
                errors["openalex"] = _describe_error(error)
                logger.warning(
                    "Exact arXiv lookup via OpenAlex failed for %s: %s",
                    arxiv_id,
                    errors["openalex"],
                )
                return None

        # 默认 all 模式优先快速解析；显式单源模式先尊重用户指定来源。
        resolver_first = len(target_sources) > 1
        if resolver_first:
            resolved_paper = await resolve_openalex()
            if resolved_paper is not None:
                return [resolved_paper]

        for name in ("arxiv", "semantic_scholar"):
            if name not in target_sources:
                continue
            lookup_id = (
                f"arxiv:{arxiv_id}"
                if name == "arxiv"
                else f"ARXIV:{arxiv_id}"
            )
            try:
                paper = await self.sources[name].fetch_by_id(lookup_id)
                successful_sources += 1
            except Exception as error:
                errors[name] = _describe_error(error)
                log = (
                    logger.info
                    if isinstance(error, SourceTemporarilyUnavailable)
                    else logger.warning
                )
                log(
                    "Exact arXiv lookup via %s failed for %s: %s",
                    name,
                    arxiv_id,
                    errors[name],
                )
                continue
            if paper is not None:
                return [paper]

        if not resolver_first:
            resolved_paper = await resolve_openalex()
            if resolved_paper is not None:
                return [resolved_paper]

        if successful_sources == 0 and errors:
            raise PaperRetrievalError(errors)
        return []

    async def fetch_by_id(self, source_id: str) -> PaperMetadata | None:
        """通过来源 ID 获取论文详情。

        自动识别前缀:
        - arxiv:XXXX → ArxivSource
        - 其他 → SemanticScholarSource
        """
        arxiv_id = normalize_arxiv_id(source_id)
        if arxiv_id:
            papers = await self._fetch_exact_arxiv_id(
                arxiv_id,
                ["arxiv", "semantic_scholar"],
            )
            return papers[0] if papers else None

        source = self.sources.get("semantic_scholar")
        if source is None:
            return None
        return await source.fetch_by_id(source_id)

    def parse_pdf(self, pdf_path: Path | str) -> PaperMetadata:
        """解析 PDF 文件。"""
        return self.parser.parse(Path(pdf_path))

    def parse_pdf_bytes(self, pdf_bytes: bytes) -> PaperMetadata:
        """解析 PDF 字节数据。"""
        return self.parser.parse_bytes(pdf_bytes)

    def _deduplicate(self, papers: list[PaperMetadata]) -> list[PaperMetadata]:
        """基于 DOI > arXiv ID > title > source ID 去重。"""
        seen: set[str] = set()
        unique: list[PaperMetadata] = []

        for p in papers:
            key = (
                (p.doi or "")
                or (f"arxiv:{p.arxiv_id}" if p.arxiv_id else "")
                or (p.title[:80].lower() if p.title else "")
                or (p.source_id or "")
            )
            if key and key not in seen:
                seen.add(key)
                unique.append(p)
            elif not key:
                # 没有去重键的论文直接保留
                unique.append(p)

        return unique
