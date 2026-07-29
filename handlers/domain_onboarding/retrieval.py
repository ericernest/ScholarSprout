"""真实论文数据源接口及 Semantic Scholar 实现。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic, perf_counter, sleep
from typing import Any, Callable, Protocol

import httpx
from pydantic import ValidationError

from .retrieval_resilience import (
    ProviderCircuitBreaker,
    ResilientHTTPClient,
    RetrievalRetryPolicy,
    TTLQueryCache,
)
from .schemas import (
    PaperCandidate,
    ProviderRetrievalStats,
    RetrievalResult,
    RetrievalStats,
)


class PaperRetrievalError(RuntimeError):
    def __init__(self, message: str, *, stats: RetrievalStats | None = None):
        super().__init__(message)
        self.stats = stats or RetrievalStats(errors=[message])


class PaperRetriever(Protocol):
    def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult: ...


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

    def _begin_search(self) -> None:
        self._http.reset_stats()

    @staticmethod
    def _cache_key(query: str, limit_per_query: int) -> str:
        return f"{limit_per_query}\0{query.strip()}"

    def _cached(self, query: str, limit_per_query: int) -> list[PaperCandidate] | None:
        return self._cache.get(self._cache_key(query, limit_per_query))

    def _store(self, query: str, limit_per_query: int, papers: list[PaperCandidate]) -> None:
        self._cache.set(self._cache_key(query, limit_per_query), papers)

    def _finish_search(self, *, errors: list[str], cache_hits: int) -> RetrievalStats:
        return RetrievalStats(
            errors=errors,
            retry_count=self._http.retry_count,
            cache_hit_count=cache_hits,
            request_count=self._http.request_count,
            rate_limit_count=self._http.rate_limit_count,
        )

    def stale_results(
        self,
        queries: list[str],
        *,
        limit_per_query: int,
        max_stale_seconds: float,
    ) -> list[PaperCandidate]:
        papers: list[PaperCandidate] = []
        for query in queries:
            cached = self._cache.get_stale(
                self._cache_key(query, limit_per_query),
                max_stale_seconds=max_stale_seconds,
            )
            if cached is not None:
                papers.extend(cached)
        return papers

    def clear_cache(self) -> None:
        self._cache.clear()


class SemanticScholarRetriever(_ResilientRetriever):
    source_name = "semantic_scholar"
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"
    paper_endpoint = "https://api.semanticscholar.org/graph/v1/paper"

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

    def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
        with self._search_lock:
            self._begin_search()
            results: list[PaperCandidate] = []
            errors: list[str] = []
            cache_hits = 0
            for query in queries:
                cached = self._cached(query, limit_per_query)
                if cached is not None:
                    cache_hits += 1
                    results.extend(cached)
                    continue
                query_results: list[PaperCandidate] = []
                try:
                    exact_arxiv = re.fullmatch(
                        r"\s*arxiv\s*:\s*(\d{4}\.\d{4,5})(?:v\d+)?\s*",
                        query,
                        re.IGNORECASE,
                    )
                    fields = "paperId,title,abstract,year,url,citationCount,authors,externalIds,publicationTypes"
                    if exact_arxiv:
                        response = self._http.get(
                            f"{self.paper_endpoint}/ARXIV:{exact_arxiv.group(1)}",
                            params={"fields": fields},
                        )
                        payload = response.json()
                        data = [payload] if payload else []
                    else:
                        response = self._http.get(
                            self.endpoint,
                            params={
                                "query": query,
                                "limit": limit_per_query,
                                "fields": fields,
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
                    errors.append(f"{query}: {error}")
            stats = self._finish_search(errors=errors, cache_hits=cache_hits)
            if not results and errors:
                raise PaperRetrievalError("all paper queries failed", stats=stats)
            return RetrievalResult(papers=results, stats=stats)

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
                publication_types=item.get("publicationTypes") or [],
            )
        except ValidationError:
            return None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class ArxivRetriever(_ResilientRetriever):
    source_name = "arxiv"
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

    def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
        with self._search_lock:
            self._begin_search()
            results: list[PaperCandidate] = []
            errors: list[str] = []
            cache_hits = 0
            for query in queries:
                cached = self._cached(query, limit_per_query)
                if cached is not None:
                    cache_hits += 1
                    results.extend(cached)
                    continue
                try:
                    exact_arxiv = re.fullmatch(
                        r"\s*arxiv\s*:\s*(\d{4}\.\d{4,5})(?:v\d+)?\s*",
                        query,
                        re.IGNORECASE,
                    )
                    params: dict[str, Any]
                    if exact_arxiv:
                        params = {
                            "id_list": exact_arxiv.group(1),
                            "start": 0,
                            "max_results": 1,
                        }
                    else:
                        params = {
                            "search_query": f"all:{query}",
                            "start": 0,
                            "max_results": limit_per_query,
                            "sortBy": "relevance",
                            "sortOrder": "descending",
                        }
                    response = self._http.get(
                        self.endpoint,
                        params=params,
                    )
                    query_results = self._parse_feed(response.text, query)
                    self._store(query, limit_per_query, query_results)
                    results.extend(query_results)
                except (httpx.HTTPError, ET.ParseError, ValueError, TypeError) as error:
                    errors.append(f"{query}: {error}")
            stats = self._finish_search(errors=errors, cache_hits=cache_hits)
            if not results and errors:
                raise PaperRetrievalError("all arXiv queries failed", stats=stats)
            return RetrievalResult(papers=results, stats=stats)

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
                        publication_types=["Preprint"],
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
    source_name = "crossref"
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

    def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
        with self._search_lock:
            self._begin_search()
            results: list[PaperCandidate] = []
            errors: list[str] = []
            cache_hits = 0
            for query in queries:
                cached = self._cached(query, limit_per_query)
                if cached is not None:
                    cache_hits += 1
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
                    errors.append(f"{query}: {error}")
            stats = self._finish_search(errors=errors, cache_hits=cache_hits)
            if not results and errors:
                raise PaperRetrievalError("all Crossref queries failed", stats=stats)
            return RetrievalResult(papers=results, stats=stats)

    def _parse_work(self, item: dict[str, Any], query: str) -> PaperCandidate | None:
        work_type = str(item.get("type") or "").strip().lower()
        if work_type and work_type not in {
            "journal-article",
            "proceedings-article",
            "posted-content",
            "book-chapter",
            "book-section",
            "dissertation",
            "report",
            "report-component",
            "monograph",
        }:
            return None
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
                publication_types=[work_type] if work_type else [],
            )
        except ValidationError:
            return None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class CompositePaperRetriever:
    """并发调用独立来源，隔离失败，并保持来源声明顺序合并结果。"""

    def __init__(
        self,
        retrievers: list[PaperRetriever],
        *,
        max_workers: int | None = None,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 30.0,
        stale_cache_seconds: float = 86400.0,
        max_queries_per_source: int | None = None,
        clock: Callable[[], float] = monotonic,
    ):
        if not retrievers:
            raise ValueError("at least one paper retriever is required")
        self.retrievers = retrievers
        self.max_workers = max_workers or len(retrievers)
        if self.max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.stale_cache_seconds = stale_cache_seconds
        if max_queries_per_source is not None and max_queries_per_source < 1:
            raise ValueError("max_queries_per_source must be positive")
        self.max_queries_per_source = max_queries_per_source
        self._clock = clock
        self._circuits = {
            self._source_name(retriever): ProviderCircuitBreaker(
                failure_threshold=circuit_failure_threshold,
                cooldown_seconds=circuit_cooldown_seconds,
                clock=clock,
            )
            for retriever in retrievers
        }

    def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
        source_queries = (
            queries[: self.max_queries_per_source]
            if self.max_queries_per_source is not None
            else queries
        )
        source_batches: dict[int, list[PaperCandidate]] = {}
        combined_stats = RetrievalStats()
        workers = min(self.max_workers, len(self.retrievers))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="paper-retrieval") as executor:
            futures: dict[int, Any] = {}
            for index, retriever in enumerate(self.retrievers):
                source_name = self._source_name(retriever)
                circuit = self._circuits[source_name]
                if not circuit.allow_request():
                    stale = self._stale_results(retriever, source_queries, limit_per_query)
                    source_batches[index] = stale
                    provider = ProviderRetrievalStats(
                        provider=source_name,
                        success=False,
                        result_count=len(stale),
                        error_count=1,
                        circuit_open=True,
                        circuit_skipped=True,
                        stale_cache_used=bool(stale),
                    )
                    self._add_provider_stats(combined_stats, provider)
                    combined_stats.errors.append(f"{source_name}: circuit open")
                    continue
                futures[index] = executor.submit(
                    self._search_source,
                    retriever,
                    source_queries,
                    limit_per_query,
                )

            for index, future in futures.items():
                retriever = self.retrievers[index]
                source_name = self._source_name(retriever)
                source_result, error, latency_ms = future.result()
                if error is None and source_result is not None:
                    self._circuits[source_name].record_success()
                    source_batches[index] = source_result.papers
                    provider = self._provider_stats(
                        source_name,
                        source_result.stats,
                        success=True,
                        result_count=len(source_result.papers),
                        latency_ms=latency_ms,
                    )
                    self._add_provider_stats(combined_stats, provider)
                    combined_stats.errors.extend(
                        f"{source_name}: {message}" for message in source_result.stats.errors
                    )
                    continue

                self._circuits[source_name].record_failure()
                error_stats = error.stats if isinstance(error, PaperRetrievalError) else RetrievalStats()
                stale = self._stale_results(retriever, source_queries, limit_per_query)
                source_batches[index] = stale
                provider = self._provider_stats(
                    source_name,
                    error_stats,
                    success=False,
                    result_count=len(stale),
                    latency_ms=latency_ms,
                    circuit_open=self._circuits[source_name].is_open,
                    stale_cache_used=bool(stale),
                )
                provider.error_count = max(1, provider.error_count)
                self._add_provider_stats(combined_stats, provider)
                combined_stats.errors.append(f"{source_name}: {error}")
                combined_stats.errors.extend(
                    f"{source_name}: {message}" for message in error_stats.errors
                )

        ordered_batches = [source_batches.get(index, []) for index in range(len(self.retrievers))]
        results = self._interleave_sources(ordered_batches)
        if not results:
            raise PaperRetrievalError(
                "all configured paper data sources failed",
                stats=combined_stats,
            )
        return RetrievalResult(papers=results, stats=combined_stats)

    @staticmethod
    def _source_name(retriever: PaperRetriever) -> str:
        return str(
            getattr(retriever, "source_name", type(retriever).__name__)
        ).strip().lower()

    @staticmethod
    def _search_source(
        retriever: PaperRetriever,
        queries: list[str],
        limit_per_query: int,
    ) -> tuple[RetrievalResult | None, Exception | None, float]:
        started = perf_counter()
        try:
            result = retriever.search(queries, limit_per_query=limit_per_query)
            return result, None, round((perf_counter() - started) * 1000, 3)
        except Exception as error:
            return None, error, round((perf_counter() - started) * 1000, 3)

    def _stale_results(
        self,
        retriever: PaperRetriever,
        queries: list[str],
        limit_per_query: int,
    ) -> list[PaperCandidate]:
        stale = getattr(retriever, "stale_results", None)
        if not callable(stale):
            return []
        return stale(
            queries,
            limit_per_query=limit_per_query,
            max_stale_seconds=self.stale_cache_seconds,
        )

    @staticmethod
    def _provider_stats(
        source_name: str,
        stats: RetrievalStats,
        *,
        success: bool,
        result_count: int,
        latency_ms: float,
        circuit_open: bool = False,
        stale_cache_used: bool = False,
    ) -> ProviderRetrievalStats:
        return ProviderRetrievalStats(
            provider=source_name,
            success=success,
            latency_ms=latency_ms,
            result_count=result_count,
            error_count=len(stats.errors),
            retry_count=stats.retry_count,
            cache_hit_count=stats.cache_hit_count,
            request_count=stats.request_count,
            rate_limit_count=stats.rate_limit_count,
            circuit_open=circuit_open,
            stale_cache_used=stale_cache_used,
        )

    @staticmethod
    def _add_provider_stats(
        combined: RetrievalStats,
        provider: ProviderRetrievalStats,
    ) -> None:
        combined.providers[provider.provider] = provider
        combined.source_success_count += int(provider.success)
        combined.source_failure_count += int(not provider.success)
        combined.retry_count += provider.retry_count
        combined.cache_hit_count += provider.cache_hit_count
        combined.request_count += provider.request_count
        combined.rate_limit_count += provider.rate_limit_count
        combined.stale_cache_hit_count += int(provider.stale_cache_used)
        combined.circuit_open_count += int(provider.circuit_open)

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
