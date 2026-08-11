"""候选论文的确定性去重、验证、多信号评分与多样性选取。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import unquote, urlparse

from .canonical_papers import CanonicalPaperRegistry
from .config import DomainOnboardingConfig
from .domain_context import DomainContextGuard
from .schemas import (
    DomainResearchPlan,
    PaperCandidate,
    PaperRole,
    ReadingPriority,
    RankedPaper,
    RankingResult,
    RankingStats,
)
from .text_similarity import (
    MultilingualEvidenceTextVectorizer,
    TextVectorizer,
    cosine_similarity,
)


class PaperRanker(Protocol):
    def rank(
        self,
        papers: list[PaperCandidate],
        plan: DomainResearchPlan,
        *,
        limit: int,
    ) -> RankingResult: ...


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower())


class WeightedPaperRanker:
    def __init__(
        self,
        config: DomainOnboardingConfig,
        vectorizer: TextVectorizer | None = None,
        fallback_vectorizer: TextVectorizer | None = None,
        context_guard: DomainContextGuard | None = None,
        canonical_registry: CanonicalPaperRegistry | None = None,
    ):
        self.config = config
        self.vectorizer = vectorizer or MultilingualEvidenceTextVectorizer()
        self.fallback_vectorizer = (
            fallback_vectorizer
            if fallback_vectorizer is not None
            else (
                MultilingualEvidenceTextVectorizer()
                if vectorizer is not None
                else None
            )
        )
        self.context_guard = context_guard or DomainContextGuard()
        self.canonical_registry = canonical_registry or CanonicalPaperRegistry()

    def rank(
        self,
        papers: list[PaperCandidate],
        plan: DomainResearchPlan,
        *,
        limit: int,
    ) -> RankingResult:
        unique = self._deduplicate(papers)
        valid = [paper for paper in unique if self._is_valid(paper)]
        invalid_count = len(unique) - len(valid)
        valid = self._limit_candidates_by_source(
            valid,
            self.config.candidate_paper_limit,
        )
        source_counts = dict(
            sorted(
                (
                    (source, sum(1 for paper in valid if paper.source == source))
                    for source in {paper.source for paper in valid}
                ),
                key=lambda item: item[0],
            )
        )
        vectorizer_backend = self._vectorizer_name(self.vectorizer)
        if not valid:
            return RankingResult(
                stats=RankingStats(
                    deduplicated_count=len(unique),
                    invalid_count=invalid_count,
                    candidate_source_counts=source_counts,
                    vectorizer_backend=vectorizer_backend,
                )
            )

        query_text = " ".join(
            [
                plan.normalized_domain,
                plan.translated_domain,
                *plan.expanded_terms,
                *plan.search_queries,
                *plan.expected_subdirections,
                *(question for perspective in plan.perspectives for question in perspective.questions),
            ]
        )
        path_texts = [
            " ".join(
                [
                    plan.translated_domain or plan.normalized_domain,
                    *plan.expanded_terms,
                    perspective.name,
                    perspective.description,
                    *perspective.questions,
                    *perspective.search_queries,
                ]
            )
            for perspective in plan.perspectives
        ]
        document_texts = [f"{paper.title} {paper.title} {paper.abstract or ''}" for paper in valid]
        vectors, vectorizer_backend, fallback_used = self._vectorize(
            [query_text, *path_texts, *document_texts]
        )
        document_offset = 1 + len(path_texts)
        if len(vectors) != len(valid) + document_offset:
            raise ValueError("text vectorizer returned an unexpected number of vectors")
        query_vector = vectors[0]
        path_vectors = vectors[1:document_offset]
        document_vectors = vectors[document_offset:]
        path_scores_by_paper = {
            paper.paper_id: {
                perspective.path_id: cosine_similarity(path_vector, paper_vector)
                for perspective, path_vector in zip(
                    plan.perspectives, path_vectors, strict=True
                )
            }
            for paper, paper_vector in zip(valid, document_vectors, strict=True)
        }
        path_pool_size = max(
            1,
            math.ceil(self.config.selected_paper_limit / len(plan.perspectives))
            * self.config.ranking_path_pool_multiplier,
        )
        path_memberships: dict[str, list[str]] = {paper.paper_id: [] for paper in valid}
        rrf_scores: dict[str, float] = {paper.paper_id: 0.0 for paper in valid}
        per_path_candidate_counts: dict[str, int] = {}
        for perspective in plan.perspectives:
            path_id = perspective.path_id
            path_ranking = sorted(
                valid,
                key=lambda paper: (
                    path_scores_by_paper[paper.paper_id][path_id],
                    paper.citation_count or 0,
                ),
                reverse=True,
            )
            path_pool = [
                paper
                for paper in path_ranking[:path_pool_size]
                if path_scores_by_paper[paper.paper_id][path_id] > 0.0
            ]
            per_path_candidate_counts[path_id] = len(path_pool)
            for rank, paper in enumerate(path_pool, start=1):
                path_memberships[paper.paper_id].append(path_id)
                rrf_scores[paper.paper_id] += 1.0 / (self.config.ranking_rrf_k + rank)
        max_rrf = len(plan.perspectives) / (self.config.ranking_rrf_k + 1)
        path_fusion_scores = {
            paper_id: min(1.0, score / max_rrf) if max_rrf else 0.0
            for paper_id, score in rrf_scores.items()
        }
        ranked: list[RankedPaper] = []
        vector_by_id = {
            paper.paper_id: vector for paper, vector in zip(valid, document_vectors, strict=True)
        }
        for paper, paper_vector in zip(valid, document_vectors, strict=True):
            global_relevance = cosine_similarity(query_vector, paper_vector)
            paper_path_scores = path_scores_by_paper[paper.paper_id]
            semantic_relevance = max(
                global_relevance,
                max(paper_path_scores.values(), default=0.0),
            )
            context_score = self.context_guard.score(paper, plan)
            relevance = semantic_relevance * context_score
            recency = self._recency_score(paper.year)
            nearest_neighbor = max(
                (
                    cosine_similarity(paper_vector, other_vector)
                    for other_id, other_vector in vector_by_id.items()
                    if other_id != paper.paper_id
                ),
                default=0.0,
            )
            diversity = 1.0 - nearest_neighbor
            canonical = self.canonical_registry.match(paper, plan.normalized_domain)
            if canonical is not None:
                relevance = max(
                    relevance, self.config.ranking_canonical_relevance_floor
                )
            role = canonical.role if canonical else self._classify_role(paper)
            reading_priority = self._reading_priority(role, canonical is not None)
            base_score = (
                self.config.relevance_weight * relevance
                + self.config.recency_weight * recency
                + self.config.diversity_weight * diversity
            )
            if not (paper.abstract or "").strip() and canonical is None:
                base_score *= self.config.ranking_missing_abstract_penalty
            path_fusion_score = path_fusion_scores[paper.paper_id]
            final = (
                (1.0 - self.config.ranking_path_fusion_weight) * base_score
                + self.config.ranking_path_fusion_weight * path_fusion_score
            )
            ranked.append(
                RankedPaper(
                    **paper.model_dump(),
                    relevance_score=round(relevance, 6),
                    context_score=round(context_score, 6),
                    recency_score=round(recency, 6),
                    diversity_score=round(diversity, 6),
                    final_score=round(min(1.0, final), 6),
                    base_score=round(min(1.0, base_score), 6),
                    path_fusion_score=round(path_fusion_score, 6),
                    path_relevance_scores={
                        path_id: round(score, 6)
                        for path_id, score in paper_path_scores.items()
                    },
                    matched_research_paths=path_memberships[paper.paper_id],
                    paper_role=role,
                    reading_priority=reading_priority,
                    is_canonical=canonical is not None,
                )
            )
        ranked.sort(key=lambda item: (item.final_score, item.citation_count or 0), reverse=True)
        context_filtered = [paper for paper in ranked if paper.context_score > 0.0]
        low_relevance_filtered_count = len(ranked) - len(context_filtered)
        ranked = context_filtered
        relevance_filtered = [
            paper
            for paper in ranked
            if paper.is_canonical
            or paper.relevance_score >= self.config.ranking_min_relevance_score
        ]
        # Never let recency/diversity turn a batch with zero lexical or semantic
        # grounding into plausible recommendations.  This used to retain every
        # candidate when *all* papers missed the relevance threshold, which is
        # especially harmful when a broad provider query returns unrelated
        # Chinese-language records.  Returning no papers lets the pipeline
        # supplement retrieval or report retrieval_failed instead of fabricating
        # a credible-looking reading list.
        low_relevance_filtered_count += len(ranked) - len(relevance_filtered)
        ranked = relevance_filtered
        abstract_ready = [
            paper
            for paper in ranked
            if paper.is_canonical or bool((paper.abstract or "").strip())
        ]
        if len(abstract_ready) >= self.config.ranking_min_abstract_candidates:
            low_relevance_filtered_count += len(ranked) - len(abstract_ready)
            ranked = abstract_ready
        per_role_candidate_counts = {
            role: sum(paper.paper_role == role for paper in ranked)
            for role in self.config.ranking_required_roles
        }
        role_balanced_pool = self._build_role_balanced_pool(
            ranked,
            min(limit, self.config.selected_paper_limit),
        )
        selected, mmr_scores = self._select_mmr(
            role_balanced_pool,
            vector_by_id,
            min(limit, self.config.selected_paper_limit),
        )
        selected_path_counts = {
            perspective.path_id: sum(
                perspective.path_id in paper.matched_research_paths for paper in selected
            )
            for perspective in plan.perspectives
        }
        return RankingResult(
            papers=selected,
            stats=RankingStats(
                deduplicated_count=len(unique),
                invalid_count=invalid_count,
                candidate_source_counts=source_counts,
                mmr_scores=mmr_scores,
                vectorizer_backend=vectorizer_backend,
                vectorizer_fallback_used=fallback_used,
                low_relevance_filtered_count=low_relevance_filtered_count,
                covered_roles=sorted({paper.paper_role for paper in selected}),
                missing_required_roles=[
                    role
                    for role in self.config.ranking_required_roles
                    if role not in {paper.paper_role for paper in selected}
                ],
                ranking_strategy="role_stratified_per_path_rrf_then_global_mmr",
                per_path_candidate_counts=per_path_candidate_counts,
                selected_path_counts=selected_path_counts,
                per_role_candidate_counts=per_role_candidate_counts,
                selected_role_counts={
                    role: sum(paper.paper_role == role for paper in selected)
                    for role in sorted({paper.paper_role for paper in selected})
                },
            ),
        )

    def _vectorize(
        self,
        texts: list[str],
    ) -> tuple[list[dict[str, float]], str, bool]:
        try:
            return self.vectorizer.vectorize(texts), self._vectorizer_name(self.vectorizer), False
        except Exception:
            if self.fallback_vectorizer is None:
                raise
            return (
                self.fallback_vectorizer.vectorize(texts),
                self._vectorizer_name(self.fallback_vectorizer),
                True,
            )

    @staticmethod
    def _vectorizer_name(vectorizer: TextVectorizer) -> str:
        return str(getattr(vectorizer, "name", type(vectorizer).__name__)).strip().lower()

    def _limit_candidates_by_source(
        self,
        papers: list[PaperCandidate],
        limit: int,
    ) -> list[PaperCandidate]:
        """在候选上限内同时轮询角色桶与来源，避免早期查询挤占候选池。"""
        if limit <= 0:
            return []
        role_order: list[PaperRole] = [
            *self.config.ranking_required_roles,
            "application",
            "other",
        ]
        by_role_source: dict[PaperRole, dict[str, list[PaperCandidate]]] = {}
        for paper in papers:
            role = next(
                (
                    candidate
                    for candidate in role_order
                    if candidate in paper.matched_role_hints
                ),
                "other",
            )
            by_role_source.setdefault(role, {}).setdefault(paper.source, []).append(paper)
        offsets = {
            (role, source): 0
            for role, sources in by_role_source.items()
            for source in sources
        }
        limited: list[PaperCandidate] = []
        while len(limited) < limit:
            added = False
            for role in role_order:
                for source, batch in by_role_source.get(role, {}).items():
                    offset = offsets[(role, source)]
                    if offset >= len(batch):
                        continue
                    limited.append(batch[offset])
                    offsets[(role, source)] = offset + 1
                    added = True
                    if len(limited) >= limit:
                        break
                if len(limited) >= limit:
                    break
            if not added:
                break
        return limited

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
                existing.matched_role_hints = list(
                    dict.fromkeys(
                        [*existing.matched_role_hints, *paper.matched_role_hints]
                    )
                )
                existing.matched_path_hints = list(
                    dict.fromkeys(
                        [*existing.matched_path_hints, *paper.matched_path_hints]
                    )
                )
                if (paper.citation_count or 0) > (existing.citation_count or 0):
                    existing.citation_count = paper.citation_count
                if not existing.abstract and paper.abstract:
                    existing.abstract = paper.abstract
            for key in keys:
                aliases[key] = canonical
        return list(merged.values())

    def _is_valid(self, paper: PaperCandidate) -> bool:
        if not (
            paper.paper_id.strip()
            and paper.title.strip()
            and paper.url.startswith(("http://", "https://"))
            and (paper.year is not None or not self.config.require_verified_paper_year)
        ):
            return False
        if paper.source == "crossref":
            return bool(
                paper.doi
                and paper.paper_id.lower() == f"doi:{paper.doi}"
                and self._url_matches_doi(paper.url, paper.doi)
            )
        if paper.source == "arxiv":
            return bool(
                paper.arxiv_id
                and paper.paper_id.lower() == f"arxiv:{paper.arxiv_id}"
                and self._url_matches_arxiv(paper.url, paper.arxiv_id)
            )
        excluded_types = {"dataset", "editorial", "lettersandcomments", "news"}
        normalized_types = {
            re.sub(r"[^a-z]", "", item.lower()) for item in paper.publication_types
        }
        return not bool(normalized_types & excluded_types)

    @staticmethod
    def _url_matches_doi(url: str, doi: str) -> bool:
        parsed = urlparse(url)
        if parsed.hostname not in {"doi.org", "dx.doi.org"}:
            return False
        return unquote(parsed.path).lstrip("/").lower() == doi.lower()

    @staticmethod
    def _url_matches_arxiv(url: str, arxiv_id: str) -> bool:
        parsed = urlparse(url)
        if parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            return False
        path_id = parsed.path.rstrip("/").split("/")[-1]
        path_id = re.sub(r"\.pdf$", "", path_id, flags=re.IGNORECASE)
        path_id = re.sub(r"v\d+$", "", path_id, flags=re.IGNORECASE)
        return path_id.lower() == arxiv_id.lower()

    def _recency_score(self, year: int | None) -> float:
        if year is None:
            return 0.25
        current = datetime.now(timezone.utc).year
        return max(0.0, min(1.0, 1.0 - max(0, current - year) / 15.0))

    def _classify_role(self, paper: PaperCandidate) -> PaperRole:
        title = paper.title.lower()
        text = f"{title} {paper.abstract or ''}".lower()
        if re.search(
            r"\bsurvey\b|systematic (?:literature )?review|\breview (?:of|on)\b|overview of|综述",
            title,
        ):
            return "survey"
        if re.search(
            r"medical|imaging|remote sensing|segmentation|detection|forecasting|"
            r"clinical|finance|industrial|application|legal|litigation|ehr|"
            r"mammograph|image generation|应用|医学|遥感|法律",
            text,
        ):
            return "application"
        if re.search(r"benchmark|evaluation|evaluating|dataset|metrics?|评测", title):
            return "evaluation"
        year = paper.year or 0
        current = datetime.now(timezone.utc).year
        if "foundational" in paper.matched_role_hints and year <= current - 3:
            return "foundational"
        if year and year <= current - 8 and (paper.citation_count or 0) >= 100:
            return "foundational"
        if "frontier" in paper.matched_role_hints and year >= current - 2:
            return "frontier"
        if "evaluation" in paper.matched_role_hints and re.search(
            r"benchmark|evaluation|evaluating|dataset|metrics?|评测",
            text,
        ):
            return "evaluation"
        if "method" in paper.matched_role_hints:
            return "method"
        if re.search(r"method|model|framework|architecture|algorithm", text):
            return "method"
        if year >= current - 2:
            return "frontier"
        return "other"

    def _build_role_balanced_pool(
        self,
        ranked: list[RankedPaper],
        limit: int,
    ) -> list[RankedPaper]:
        if limit <= 0 or len(ranked) <= limit:
            return ranked
        available_roles = [
            role
            for role in self.config.ranking_required_roles
            if any(paper.paper_role == role for paper in ranked)
        ]
        if not available_roles:
            return ranked
        per_role_limit = max(
            1,
            math.ceil(limit / len(available_roles))
            * self.config.ranking_role_pool_multiplier,
        )
        pool_ids = {paper.paper_id for paper in ranked[:limit]}
        for role in available_roles:
            role_ids = [
                paper.paper_id for paper in ranked if paper.paper_role == role
            ][:per_role_limit]
            pool_ids.update(role_ids)
        return [paper for paper in ranked if paper.paper_id in pool_ids]

    @staticmethod
    def _reading_priority(role: PaperRole, is_canonical: bool) -> ReadingPriority:
        if is_canonical or role == "foundational":
            return "core"
        if role in {"survey", "method", "evaluation"}:
            return "recommended"
        if role in {"frontier", "application"}:
            return "optional"
        return "extended"

    def _select_mmr(
        self,
        ranked: list[RankedPaper],
        vectors: dict[str, dict[str, float]],
        limit: int,
    ) -> tuple[list[RankedPaper], dict[str, float]]:
        if limit <= 0:
            return [], {}
        mmr_scores: dict[str, float] = {}
        selected: list[RankedPaper] = []
        remaining = list(ranked)
        covered_roles: set[PaperRole] = set()
        core_selected = False
        available_required_roles = [
            role
            for role in self.config.ranking_required_roles
            if any(paper.paper_role == role for paper in remaining)
        ]
        target_role_count = min(
            self.config.ranking_min_role_coverage,
            len(available_required_roles),
            limit,
        )
        while remaining and len(selected) < limit:
            scored: list[tuple[float, float, float, int, RankedPaper]] = []
            application_count = sum(
                item.paper_role == "application" for item in selected
            )
            survey_count = sum(item.paper_role == "survey" for item in selected)
            evaluation_count = sum(
                item.paper_role == "evaluation" for item in selected
            )
            method_count = sum(item.paper_role == "method" for item in selected)
            eligible_remaining = [
                paper
                for paper in remaining
                if (
                    paper.paper_role != "application"
                    or application_count < self.config.ranking_max_application_papers
                )
                and (
                    paper.paper_role != "survey"
                    or survey_count < self.config.ranking_max_survey_papers
                )
                and (
                    paper.paper_role != "evaluation"
                    or evaluation_count < self.config.ranking_max_evaluation_papers
                )
                and (
                    paper.paper_role != "method"
                    or method_count < self.config.ranking_max_method_papers
                )
            ]
            if not eligible_remaining:
                break
            for index, paper in enumerate(eligible_remaining):
                redundancy = max(
                    (
                        cosine_similarity(vectors[paper.paper_id], vectors[item.paper_id])
                        for item in selected
                    ),
                    default=0.0,
                )
                novelty = 1.0 - redundancy
                role_bonus = (
                    self.config.mmr_role_bonus
                    if paper.paper_role != "other" and paper.paper_role not in covered_roles
                    else 0.0
                )
                uncovered_required = {
                    role for role in available_required_roles if role not in covered_roles
                }
                role_gate = (
                    1.0
                    if len(covered_roles.intersection(available_required_roles)) < target_role_count
                    and paper.paper_role in uncovered_required
                    else 0.0
                )
                core_gate = 1.0 if paper.reading_priority == "core" and not core_selected else 0.0
                mmr_score = (
                    self.config.mmr_lambda * paper.final_score
                    + (1.0 - self.config.mmr_lambda) * novelty
                    + role_bonus
                )
                scored.append(
                    (core_gate, role_gate, mmr_score, paper.final_score, -index, paper)
                )
            _, _, mmr_score, _, _, chosen = max(scored, key=lambda item: item[:5])
            selected.append(chosen)
            covered_roles.add(chosen.paper_role)
            core_selected = core_selected or chosen.reading_priority == "core"
            mmr_scores[chosen.paper_id] = round(mmr_score, 6)
            remaining.remove(chosen)
        return selected, mmr_scores
