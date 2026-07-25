"""候选论文的确定性去重、验证、多信号评分与多样性选取。"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Protocol

from .config import DomainOnboardingConfig
from .schemas import DomainResearchPlan, PaperCandidate, PaperRole, RankedPaper


class PaperRanker(Protocol):
    last_deduplicated_count: int
    last_invalid_count: int

    def rank(self, papers: list[PaperCandidate], plan: DomainResearchPlan, *, limit: int) -> list[RankedPaper]: ...


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text.lower()))


class WeightedPaperRanker:
    def __init__(self, config: DomainOnboardingConfig):
        self.config = config
        self.last_deduplicated_count = 0
        self.last_invalid_count = 0

    def rank(self, papers: list[PaperCandidate], plan: DomainResearchPlan, *, limit: int) -> list[RankedPaper]:
        unique = self._deduplicate(papers)
        self.last_deduplicated_count = len(unique)
        valid = [paper for paper in unique if self._is_valid(paper)]
        self.last_invalid_count = len(unique) - len(valid)
        valid = valid[: self.config.candidate_paper_limit]
        if not valid:
            return []

        max_citations = max((paper.citation_count or 0) for paper in valid)
        query_tokens = _tokens(
            " ".join(
                [plan.normalized_domain, *plan.search_queries, *plan.expected_subdirections]
            )
        )
        ranked: list[RankedPaper] = []
        seen_topics: set[str] = set()
        for paper in valid:
            text_tokens = _tokens(f"{paper.title} {paper.abstract or ''}")
            overlap = len(query_tokens & text_tokens)
            relevance = min(1.0, overlap / max(4, min(12, len(query_tokens))))
            citations = (
                math.log1p(paper.citation_count or 0) / math.log1p(max_citations)
                if max_citations > 0 else 0.0
            )
            recency = self._recency_score(paper.year)
            novel = text_tokens - seen_topics
            diversity = min(1.0, len(novel) / max(5, len(text_tokens))) if text_tokens else 0.0
            role = self._classify_role(paper)
            final = (
                self.config.relevance_weight * relevance
                + self.config.citation_weight * citations
                + self.config.recency_weight * recency
                + self.config.diversity_weight * diversity
            )
            ranked.append(
                RankedPaper(
                    **paper.model_dump(),
                    relevance_score=round(relevance, 6),
                    citation_score=round(citations, 6),
                    recency_score=round(recency, 6),
                    diversity_score=round(diversity, 6),
                    final_score=round(min(1.0, final), 6),
                    paper_role=role,
                )
            )
            seen_topics.update(text_tokens)
        ranked.sort(key=lambda item: (item.final_score, item.citation_count or 0), reverse=True)
        return self._select_diverse(ranked, min(limit, self.config.selected_paper_limit))

    def _deduplicate(self, papers: list[PaperCandidate]) -> list[PaperCandidate]:
        merged: dict[str, PaperCandidate] = {}
        aliases: dict[str, str] = {}
        for paper in papers:
            keys = [
                f"id:{paper.paper_id.lower()}",
                f"title:{_normalize_title(paper.title)}",
            ]
            if paper.doi:
                keys.append(f"doi:{paper.doi.lower()}")
            if paper.arxiv_id:
                keys.append(f"arxiv:{paper.arxiv_id.lower()}")
            canonical = next((aliases[key] for key in keys if key in aliases), keys[0])
            if canonical not in merged:
                merged[canonical] = paper.model_copy(deep=True)
            else:
                existing = merged[canonical]
                existing.matched_queries = list(dict.fromkeys([*existing.matched_queries, *paper.matched_queries]))
                if (paper.citation_count or 0) > (existing.citation_count or 0):
                    existing.citation_count = paper.citation_count
                if not existing.abstract and paper.abstract:
                    existing.abstract = paper.abstract
            for key in keys:
                aliases[key] = canonical
        return list(merged.values())

    def _is_valid(self, paper: PaperCandidate) -> bool:
        return bool(paper.paper_id.strip() and paper.title.strip() and paper.url.startswith(("http://", "https://")))

    def _recency_score(self, year: int | None) -> float:
        if year is None:
            return 0.25
        current = datetime.now(timezone.utc).year
        return max(0.0, min(1.0, 1.0 - max(0, current - year) / 15.0))

    def _classify_role(self, paper: PaperCandidate) -> PaperRole:
        text = f"{paper.title} {paper.abstract or ''}".lower()
        if re.search(r"survey|review|overview|综述", text):
            return "survey"
        if re.search(r"benchmark|evaluation|evaluating|dataset|评测", text):
            return "evaluation"
        year = paper.year or 0
        current = datetime.now(timezone.utc).year
        if year and year <= current - 8 and (paper.citation_count or 0) >= 100:
            return "foundational"
        if year >= current - 2:
            return "frontier"
        if re.search(r"method|model|framework|architecture|algorithm", text):
            return "method"
        return "other"

    def _select_diverse(self, ranked: list[RankedPaper], limit: int) -> list[RankedPaper]:
        if limit <= 0:
            return []
        by_role: dict[PaperRole, list[RankedPaper]] = defaultdict(list)
        for paper in ranked:
            by_role[paper.paper_role].append(paper)
        selected: list[RankedPaper] = []
        selected_ids: set[str] = set()
        for role in ("survey", "foundational", "evaluation", "frontier", "method"):
            if by_role[role] and len(selected) < limit:
                paper = by_role[role][0]
                selected.append(paper)
                selected_ids.add(paper.paper_id)
        for paper in ranked:
            if len(selected) >= limit:
                break
            if paper.paper_id not in selected_ids:
                selected.append(paper)
                selected_ids.add(paper.paper_id)
        selected.sort(key=lambda item: item.final_score, reverse=True)
        return selected
