"""真实论文数据源接口及 Semantic Scholar 实现。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from .schemas import PaperCandidate


class PaperRetrievalError(RuntimeError):
    pass


class PaperRetriever(Protocol):
    last_errors: list[str]

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]: ...


class SemanticScholarRetriever:
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        headers = {"User-Agent": "NoviceSynapse/0.1 domain-onboarding"}
        if api_key:
            headers["x-api-key"] = api_key
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout, headers=headers, trust_env=False)
        self.last_errors: list[str] = []

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        self.last_errors = []
        results: list[PaperCandidate] = []
        for query in queries:
            try:
                response = self.client.get(
                    self.endpoint,
                    params={
                        "query": query,
                        "limit": limit_per_query,
                        "fields": "paperId,title,abstract,year,url,citationCount,authors,externalIds",
                    },
                )
                response.raise_for_status()
                data = response.json().get("data", [])
                for item in data:
                    paper = self._parse_paper(item, query)
                    if paper is not None:
                        results.append(paper)
            except (httpx.HTTPError, ValueError, TypeError) as error:
                self.last_errors.append(f"{query}: {error}")
                continue
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


class ArxivRetriever:
    endpoint = "https://export.arxiv.org/api/query"
    atom_namespace = {"atom": "http://www.w3.org/2005/Atom"}

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "NoviceSynapse/0.1 domain-onboarding contact=local-user"},
            follow_redirects=True,
            trust_env=False,
        )
        self.last_errors: list[str] = []

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        self.last_errors = []
        results: list[PaperCandidate] = []
        for query in queries:
            try:
                response = self.client.get(
                    self.endpoint,
                    params={
                        "search_query": f"all:{query}",
                        "start": 0,
                        "max_results": limit_per_query,
                        "sortBy": "relevance",
                        "sortOrder": "descending",
                    },
                )
                response.raise_for_status()
                results.extend(self._parse_feed(response.text, query))
            except (httpx.HTTPError, ET.ParseError, ValueError, TypeError) as error:
                self.last_errors.append(f"{query}: {error}")
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


class CrossrefRetriever:
    endpoint = "https://api.crossref.org/works"

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "NoviceSynapse/0.1 (mailto:local@example.invalid)"},
            follow_redirects=True,
            trust_env=False,
        )
        self.last_errors: list[str] = []

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        self.last_errors = []
        results: list[PaperCandidate] = []
        for query in queries:
            try:
                response = self.client.get(
                    self.endpoint,
                    params={
                        "query.bibliographic": query,
                        "rows": limit_per_query,
                        "select": "DOI,title,author,published-print,published-online,URL,is-referenced-by-count,abstract,type",
                    },
                )
                response.raise_for_status()
                for item in (response.json().get("message") or {}).get("items", []):
                    paper = self._parse_work(item, query)
                    if paper is not None:
                        results.append(paper)
            except (httpx.HTTPError, ValueError, TypeError) as error:
                self.last_errors.append(f"{query}: {error}")
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
    """并行扩展点的同步 V1 实现：来源失败隔离，结果交给 Ranker 去重。"""

    def __init__(self, retrievers: list[PaperRetriever]):
        if not retrievers:
            raise ValueError("at least one paper retriever is required")
        self.retrievers = retrievers
        self.last_errors: list[str] = []

    def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
        self.last_errors = []
        results: list[PaperCandidate] = []
        for retriever in self.retrievers:
            try:
                results.extend(retriever.search(queries, limit_per_query=limit_per_query))
            except PaperRetrievalError as error:
                self.last_errors.append(f"{type(retriever).__name__}: {error}")
            self.last_errors.extend(
                f"{type(retriever).__name__}: {message}"
                for message in getattr(retriever, "last_errors", [])
            )
        if not results:
            raise PaperRetrievalError("all configured paper data sources failed")
        return results

    def close(self) -> None:
        for retriever in self.retrievers:
            close = getattr(retriever, "close", None)
            if callable(close):
                close()
