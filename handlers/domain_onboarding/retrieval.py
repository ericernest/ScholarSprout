"""真实论文数据源接口及 Semantic Scholar 实现。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic, sleep
from typing import Any, Callable, Protocol

import httpx
from pydantic import ValidationError

from .retrieval_resilience import ResilientHTTPClient, RetrievalRetryPolicy, TTLQueryCache
from .schemas import PaperCandidate


class PaperRetrievalError(RuntimeError):
    pass


class PaperRetriever(Protocol):
    last_errors: list[str]
    last_retry_count: int
    last_cache_hits: int

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]: ...


class _ResilientRetriever:
    def _configure_resilience(
        self,
        *,
        client: httpx.Client,
        retry_policy: RetrievalRetryPolicy | None,
        cache_ttl_seconds: float,
        cache_max_entries: int,
        min_interval_seconds: float,
        sleep_func: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self._search_lock = Lock()
        self._http = ResilientHTTPClient(
            client,
            retry_policy=retry_policy or RetrievalRetryPolicy(),
            min_interval_seconds=min_interval_seconds,
            sleep_func=sleep_func,
            clock=clock,
        )
        self._cache: TTLQueryCache[list[PaperCandidate]] = TTLQueryCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
            clock=clock,
        )
        self.last_errors: list[str] = []
        self.last_retry_count = 0
        self.last_cache_hits = 0
        self.last_request_count = 0

    def _begin_search(self) -> None:
        self.last_errors = []
        self.last_retry_count = 0
        self.last_cache_hits = 0
        self.last_request_count = 0
        self._http.reset_stats()

    @staticmethod
    def _cache_key(query: str, limit_per_query: int) -> str:
        return f"{limit_per_query}\0{query.strip()}"

    def _cached(self, query: str, limit_per_query: int) -> list[PaperCandidate] | None:
        cached = self._cache.get(self._cache_key(query, limit_per_query))
        if cached is not None:
            self.last_cache_hits += 1
        return cached

    def _store(self, query: str, limit_per_query: int, papers: list[PaperCandidate]) -> None:
        self._cache.set(self._cache_key(query, limit_per_query), papers)

    def _finish_search(self) -> None:
        self.last_retry_count = self._http.retry_count
        self.last_request_count = self._http.request_count

    def clear_cache(self) -> None:
        self._cache.clear()


class SemanticScholarRetriever(_ResilientRetriever):
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        retry_policy: RetrievalRetryPolicy | None = None,
        cache_ttl_seconds: float = 3600.0,
        cache_max_entries: int = 256,
        sleep_func: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        headers = {"User-Agent": "NoviceSynapse/0.1 domain-onboarding"}
        if api_key:
            headers["x-api-key"] = api_key
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout, headers=headers, trust_env=False)
        self._configure_resilience(
            client=self.client,
            retry_policy=retry_policy,
            cache_ttl_seconds=cache_ttl_seconds,
            cache_max_entries=cache_max_entries,
            min_interval_seconds=0.0,
            sleep_func=sleep_func,
            clock=clock,
        )

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        with self._search_lock:
            self._begin_search()
            results: list[PaperCandidate] = []
            for query in queries:
                cached = self._cached(query, limit_per_query)
                if cached is not None:
                    results.extend(cached)
                    continue
                query_results: list[PaperCandidate] = []
                try:
                    response = self._http.get(
                        self.endpoint,
                        params={
                            "query": query,
                            "limit": limit_per_query,
                            "fields": "paperId,title,abstract,year,url,citationCount,authors,externalIds",
                        },
                    )
                    data = response.json().get("data", [])
                    for item in data:
                        paper = self._parse_paper(item, query)
                        if paper is not None:
                            query_results.append(paper)
                    self._store(query, limit_per_query, query_results)
                    results.extend(query_results)
                except (httpx.HTTPError, ValueError, TypeError) as error:
                    self.last_errors.append(f"{query}: {error}")
            self._finish_search()
            if not results and self.last_errors:
                raise PaperRetrievalError("all paper queries failed")
            return results

    def _parse_paper(self, item: dict[str, Any], query: str) -> PaperCandidate | None:
        external = item.get("externalIds") or {}
        paper_id = str(item.get("paperId") or external.get("ArXiv") or external.get("DOI") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not url and paper_id:
            url = f"https://www.semanticscholar.org/paper/{paper_id}"
        try:
            return PaperCandidate(
                paper_id=paper_id,
                title=title,
                authors=[str(author.get("name") or "").strip() for author in (item.get("authors") or []) if str(author.get("name") or "").strip()],
                abstract=item.get("abstract"),
                year=item.get("year"),
                url=url,
                citation_count=item.get("citationCount"),
                source="semantic_scholar",
                matched_queries=[query],
                doi=external.get("DOI"),
                arxiv_id=external.get("ArXiv"),
            )
        except ValidationError:
            return None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class ArxivRetriever(_ResilientRetriever):
    endpoint = "https://export.arxiv.org/api/query"
    atom_namespace = {"atom": "http://www.w3.org/2005/Atom"}

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        client: httpx.Client | None = None,
        retry_policy: RetrievalRetryPolicy | None = None,
        cache_ttl_seconds: float = 3600.0,
        cache_max_entries: int = 256,
        min_interval_seconds: float = 3.0,
        sleep_func: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "NoviceSynapse/0.1 domain-onboarding contact=local-user"},
            follow_redirects=True,
            trust_env=False,
        )
        self._configure_resilience(
            client=self.client,
            retry_policy=retry_policy,
            cache_ttl_seconds=cache_ttl_seconds,
            cache_max_entries=cache_max_entries,
            min_interval_seconds=min_interval_seconds,
            sleep_func=sleep_func,
            clock=clock,
        )

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        with self._search_lock:
            self._begin_search()
            results: list[PaperCandidate] = []
            for query in queries:
                cached = self._cached(query, limit_per_query)
                if cached is not None:
                    results.extend(cached)
                    continue
                try:
                    response = self._http.get(
                        self.endpoint,
                        params={
                            "search_query": f"all:{query}",
                            "start": 0,
                            "max_results": limit_per_query,
                            "sortBy": "relevance",
                            "sortOrder": "descending",
                        },
                    )
                    query_results = self._parse_feed(response.text, query)
                    self._store(query, limit_per_query, query_results)
                    results.extend(query_results)
                except (httpx.HTTPError, ET.ParseError, ValueError, TypeError) as error:
                    self.last_errors.append(f"{query}: {error}")
            self._finish_search()
            if not results and self.last_errors:
                raise PaperRetrievalError("all arXiv queries failed")
            return results

    def _parse_feed(self, xml_text: str, query: str) -> list[PaperCandidate]:
        root = ET.fromstring(xml_text)
        papers: list[PaperCandidate] = []
        for entry in root.findall("atom:entry", self.atom_namespace):
            id_text = self._text(entry, "atom:id")
            arxiv_id = id_text.rstrip("/").split("/")[-1]
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
            title = " ".join(self._text(entry, "atom:title").split())
            abstract = " ".join(self._text(entry, "atom:summary").split()) or None
            published = self._text(entry, "atom:published")
            year = int(published[:4]) if re.match(r"^\d{4}", published) else None
            authors = [
                " ".join((author.findtext("atom:name", default="", namespaces=self.atom_namespace)).split())
                for author in entry.findall("atom:author", self.atom_namespace)
            ]
            url = id_text
            for link in entry.findall("atom:link", self.atom_namespace):
                if link.attrib.get("rel") == "alternate" and link.attrib.get("href"):
                    url = link.attrib["href"]
                    break
            doi = entry.findtext("{http://arxiv.org/schemas/atom}doi")
            try:
                papers.append(
                    PaperCandidate(
                        paper_id=f"arxiv:{arxiv_id}",
                        title=title,
                        authors=[author for author in authors if author],
                        abstract=abstract,
                        year=year,
                        url=url,
                        citation_count=None,
                        source="arxiv",
                        matched_queries=[query],
                        doi=doi,
                        arxiv_id=arxiv_id,
                    )
                )
            except ValidationError:
                continue
        return papers

    def _text(self, entry: ET.Element, path: str) -> str:
        return entry.findtext(path, default="", namespaces=self.atom_namespace).strip()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class CrossrefRetriever(_ResilientRetriever):
    endpoint = "https://api.crossref.org/works"

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        client: httpx.Client | None = None,
        retry_policy: RetrievalRetryPolicy | None = None,
        cache_ttl_seconds: float = 3600.0,
        cache_max_entries: int = 256,
        mailto: str | None = None,
        sleep_func: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "NoviceSynapse/0.1 (mailto:local@example.invalid)"},
            follow_redirects=True,
            trust_env=False,
        )
        self.mailto = mailto.strip() if mailto else None
        self._configure_resilience(
            client=self.client,
            retry_policy=retry_policy,
            cache_ttl_seconds=cache_ttl_seconds,
            cache_max_entries=cache_max_entries,
            min_interval_seconds=0.0,
            sleep_func=sleep_func,
            clock=clock,
        )

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        with self._search_lock:
            self._begin_search()
            results: list[PaperCandidate] = []
            for query in queries:
                cached = self._cached(query, limit_per_query)
                if cached is not None:
                    results.extend(cached)
                    continue
                params: dict[str, Any] = {
                    "query.bibliographic": query,
                    "rows": limit_per_query,
                    "select": "DOI,title,author,published-print,published-online,URL,is-referenced-by-count,abstract,type",
                }
                if self.mailto:
                    params["mailto"] = self.mailto
                try:
                    response = self._http.get(self.endpoint, params=params)
                    query_results: list[PaperCandidate] = []
                    for item in (response.json().get("message") or {}).get("items", []):
                        paper = self._parse_work(item, query)
                        if paper is not None:
                            query_results.append(paper)
                    self._store(query, limit_per_query, query_results)
                    results.extend(query_results)
                except (httpx.HTTPError, ValueError, TypeError) as error:
                    self.last_errors.append(f"{query}: {error}")
            self._finish_search()
            if not results and self.last_errors:
                raise PaperRetrievalError("all Crossref queries failed")
            return results

    def _parse_work(self, item: dict[str, Any], query: str) -> PaperCandidate | None:
        doi = str(item.get("DOI") or "").strip()
        titles = item.get("title") or []
        title = str(titles[0] if titles else "").strip()
        authors = []
        for author in item.get("author") or []:
            name = " ".join(
                part for part in (str(author.get("given") or "").strip(), str(author.get("family") or "").strip())
                if part
            )
            if name:
                authors.append(name)
        date = item.get("published-print") or item.get("published-online") or {}
        parts = date.get("date-parts") or []
        year = parts[0][0] if parts and parts[0] else None
        url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else "")).strip()
        try:
            return PaperCandidate(
                paper_id=f"doi:{doi.lower()}",
                title=title,
                authors=authors,
                abstract=item.get("abstract"),
                year=year,
                url=url,
                citation_count=item.get("is-referenced-by-count"),
                source="crossref",
                matched_queries=[query],
                doi=doi,
            )
        except ValidationError:
            return None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class CompositePaperRetriever:
    """并发调用独立来源，隔离失败，并保持来源声明顺序合并结果。"""

    def __init__(self, retrievers: list[PaperRetriever], *, max_workers: int | None = None):
        if not retrievers:
            raise ValueError("at least one paper retriever is required")
        self.retrievers = retrievers
        self.max_workers = max_workers or len(retrievers)
        if self.max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.last_errors: list[str] = []
        self.last_retry_count = 0
        self.last_cache_hits = 0
        self.last_request_count = 0
        self.last_source_success_count = 0
        self.last_source_failure_count = 0

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        self.last_errors = []
        self.last_retry_count = 0
        self.last_cache_hits = 0
        self.last_request_count = 0
        self.last_source_success_count = 0
        self.last_source_failure_count = 0
        source_batches: list[list[PaperCandidate]] = []
        workers = min(self.max_workers, len(self.retrievers))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="paper-retrieval") as executor:
            futures = [
                executor.submit(retriever.search, queries, limit_per_query=limit_per_query)
                for retriever in self.retrievers
            ]
            for retriever, future in zip(self.retrievers, futures, strict=True):
                source_name = type(retriever).__name__
                try:
                    source_results = future.result()
                except Exception as error:
                    self.last_source_failure_count += 1
                    self.last_errors.append(f"{source_name}: {error}")
                    source_batches.append([])
                else:
                    self.last_source_success_count += 1
                    source_batches.append(source_results)
                self.last_errors.extend(
                    f"{source_name}: {message}"
                    for message in getattr(retriever, "last_errors", [])
                )
                self.last_retry_count += int(getattr(retriever, "last_retry_count", 0))
                self.last_cache_hits += int(getattr(retriever, "last_cache_hits", 0))
                self.last_request_count += int(getattr(retriever, "last_request_count", 0))
        results = self._interleave_sources(source_batches)
        if not results:
            raise PaperRetrievalError("all configured paper data sources failed")
        return results

    @staticmethod
    def _interleave_sources(
        source_batches: list[list[PaperCandidate]],
    ) -> list[PaperCandidate]:
        """按来源轮询合并，避免声明顺序靠前的来源占满候选池。"""
        queues = [deque(batch) for batch in source_batches if batch]
        merged: list[PaperCandidate] = []
        while queues:
            active: list[deque[PaperCandidate]] = []
            for queue in queues:
                merged.append(queue.popleft())
                if queue:
                    active.append(queue)
            queues = active
        return merged

    def close(self) -> None:
        for retriever in self.retrievers:
            close = getattr(retriever, "close", None)
            if callable(close):
                close()
