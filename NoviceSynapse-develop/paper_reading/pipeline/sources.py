"""多源论文检索 — arXiv, Semantic Scholar。

设计:
- 统一 PaperSource 基类接口
- ArxivSource: 通过 arXiv API (Atom XML) 检索
- SemanticScholarSource: 通过 Semantic Scholar API (JSON) 检索
- PaperPipeline: 统一入口，支持多源并行搜索和去重
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any
from uuid import uuid4
from pathlib import Path
import feedparser
import httpx

from paper_reading.pipeline.metadata import Author, PaperMetadata
from paper_reading.pipeline.parser import PDFParser

logger = logging.getLogger(__name__)


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

    API: http://export.arxiv.org/api/query?search_query=all:{query}&max_results={n}
    返回: Atom XML，通过 feedparser 解析
    """

    SOURCE_NAME = "arxiv"
    API_URL = "http://export.arxiv.org/api/query"

    async def search(self, query: str, max_results: int = 10) -> list[PaperMetadata]:
        """通过 arXiv API 检索论文。"""
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(max_results, 50),
            "sortBy": "relevance",
        }
        retries = 2
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(self.API_URL, params=params)
                    response.raise_for_status()

                feed = feedparser.parse(response.text)
                papers: list[PaperMetadata] = []

                for entry in feed.entries:
                    arxiv_id = entry.id.split("/abs/")[-1]
                    # 清理版本号
                    if "v" in arxiv_id and arxiv_id.index("v") > 0:
                        arxiv_id = arxiv_id.split("v")[0]

                    authors = [
                        Author(name=author.get("name", ""))
                        for author in entry.get("authors", [])
                    ]

                    # 分类标签
                    categories = []
                    for tag in entry.get("tags", []):
                        if tag.get("term"):
                            categories.append(tag["term"])

                    papers.append(PaperMetadata(
                        paper_id=str(uuid4()),
                        source="arxiv",
                        source_id=f"arxiv:{arxiv_id}",
                        title=entry.get("title", "").strip().replace("\n", " "),
                        authors=authors,
                        abstract=entry.get("summary", "").strip().replace("\n", " "),
                        published_date=_parse_date(entry.get("published", "")),
                        updated_date=_parse_date(entry.get("updated", "")),
                        categories=categories,
                        doi=entry.get("arxiv_doi", "") or "",
                        url=entry.get("id", ""),
                        pdf_url=entry.get("id", "").replace("/abs/", "/pdf/"),
                        journal_ref=entry.get("arxiv_journal_ref", "") or "",
                    ))
                return papers
            except Exception as e:
                if attempt < retries:
                    logger.warning("arXiv search attempt %d failed: %s", attempt + 1, e)
                    await asyncio.sleep(1)
                else:
                    logger.error("arXiv search failed after %d retries: %s", retries, e)
                    return []

    async def fetch_by_id(self, source_id: str) -> PaperMetadata | None:
        """通过 arXiv ID 获取单篇论文。"""
        # 去除 arxiv: 前缀
        arxiv_id = source_id.replace("arxiv:", "")
        params = {
            "id_list": arxiv_id,
            "max_results": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self.API_URL, params=params)
                response.raise_for_status()
            feed = feedparser.parse(response.text)
            entries = feed.entries
            if not entries:
                return None
            entry = entries[0]
            return PaperMetadata(
                paper_id=str(uuid4()),
                source="arxiv",
                source_id=f"arxiv:{arxiv_id}",
                title=entry.get("title", "").strip().replace("\n", " "),
                authors=[
                    Author(name=a.get("name", ""))
                    for a in entry.get("authors", [])
                ],
                abstract=entry.get("summary", "").strip().replace("\n", " "),
                published_date=_parse_date(entry.get("published", "")),
                pdf_url=entry.get("id", "").replace("/abs/", "/pdf/"),
                url=entry.get("id", ""),
            )
        except Exception as e:
            logger.error("arXiv fetch_by_id failed for %s: %s", arxiv_id, e)
            return None


# ── Semantic Scholar ──

class SemanticScholarSource(PaperSource):
    """Semantic Scholar API 检索。

    API: https://api.semanticscholar.org/graph/v1/paper/search
    返回: JSON（无需 XML 解析，更高效）
    """

    SOURCE_NAME = "semantic_scholar"
    API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

    async def search(self, query: str, max_results: int = 10) -> list[PaperMetadata]:
        """通过 Semantic Scholar API 检索论文。"""
        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": "title,authors,abstract,year,externalIds,url,citationCount,venue,journal",
        }
        retries = 2
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(self.API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                break
            except Exception as e:
                if attempt < retries:
                    logger.warning("Semantic Scholar search attempt %d failed: %s", attempt + 1, e)
                    await asyncio.sleep(1)
                else:
                    logger.error("Semantic Scholar search failed: %s", e)
                    return []

        papers: list[PaperMetadata] = []
        for item in data.get("data", []):
            authors = [
                Author(name=a.get("name", ""))
                for a in item.get("authors", [])
            ]
            ext = item.get("externalIds", {}) or {}
            venue_raw = item.get("venue", "") or ""
            journal = item.get("journal", {}) or {}
            venue = (journal.get("name", "") or venue_raw)

            papers.append(PaperMetadata(
                paper_id=str(uuid4()),
                source="semantic_scholar",
                source_id=item.get("paperId", ""),
                title=item.get("title", ""),
                authors=authors,
                abstract=item.get("abstract", "") or "",
                year=item.get("year"),
                published_date=_parse_year(item.get("year")),
                citation_count=item.get("citationCount"),
                doi=ext.get("DOI", ""),
                arxiv_id=ext.get("ArXiv", ""),
                url=item.get("url", ""),
                venue=venue,
            ))
        return papers

    async def fetch_by_id(self, source_id: str) -> PaperMetadata | None:
        """通过 Semantic Scholar paper ID 获取详情。"""
        url = f"https://api.semanticscholar.org/graph/v1/paper/{source_id}"
        params = {
            "fields": "title,authors,abstract,year,externalIds,url,citationCount,venue,journal",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                item = resp.json()
        except Exception as e:
            logger.error("Semantic Scholar fetch_by_id failed for %s: %s", source_id, e)
            return None

        authors = [Author(name=a.get("name", "")) for a in item.get("authors", [])]
        ext = item.get("externalIds", {}) or {}
        return PaperMetadata(
            paper_id=str(uuid4()),
            source="semantic_scholar",
            source_id=source_id,
            title=item.get("title", ""),
            authors=authors,
            abstract=item.get("abstract", "") or "",
            year=item.get("year"),
            published_date=_parse_year(item.get("year")),
            citation_count=item.get("citationCount"),
            doi=ext.get("DOI", ""),
            url=item.get("url", ""),
        )


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
        target_sources = sources or list(self.sources.keys())
        tasks = []
        for name in target_sources:
            if name in self.sources:
                tasks.append(self.sources[name].search(query, max_results))

        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers: list[PaperMetadata] = []
        for i, result in enumerate(results_list):
            if isinstance(result, Exception):
                logger.warning("Source %s failed: %s", target_sources[i], result)
            elif isinstance(result, list):
                all_papers.extend(result)

        return self._deduplicate(all_papers)

    async def fetch_by_id(self, source_id: str) -> PaperMetadata | None:
        """通过来源 ID 获取论文详情。

        自动识别前缀:
        - arxiv:XXXX → ArxivSource
        - 其他 → SemanticScholarSource
        """
        if source_id.startswith("arxiv:"):
            source = self.sources.get("arxiv")
        else:
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
        """基于 DOI > source_id > title(前80字符) 去重。"""
        seen: set[str] = set()
        unique: list[PaperMetadata] = []

        for p in papers:
            key = (
                (p.doi or "")
                or (p.source_id or "")
                or (p.title[:80].lower() if p.title else "")
            )
            if key and key not in seen:
                seen.add(key)
                unique.append(p)
            elif not key:
                # 没有去重键的论文直接保留
                unique.append(p)

        return unique
