"""在生成前识别论文的子方向与角色覆盖缺口。"""

from __future__ import annotations

from typing import Protocol

from .config import DomainOnboardingConfig
from .schemas import (
    CoverageAnalysis,
    CoverageGap,
    DomainResearchPlan,
    PaperRole,
    RankedPaper,
    stable_id,
)
from .text_similarity import TextVectorizer, TfidfTextVectorizer, cosine_similarity


class CoverageAnalyzer(Protocol):
    def analyze(
        self,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
    ) -> CoverageAnalysis: ...


class PaperCoverageAnalyzer:
    required_global_roles: tuple[PaperRole, ...] = (
        "survey",
        "foundational",
        "evaluation",
        "frontier",
    )
    role_query_terms = {
        "survey": "survey review overview",
        "foundational": "foundational seminal early work",
        "method": "method model framework",
        "evaluation": "benchmark evaluation dataset",
        "application": "application applied case study",
        "frontier": "recent advances state of the art",
        "other": "research paper",
    }

    def __init__(
        self,
        config: DomainOnboardingConfig,
        vectorizer: TextVectorizer | None = None,
        fallback_vectorizer: TextVectorizer | None = None,
    ) -> None:
        self.config = config
        self.vectorizer = vectorizer or TfidfTextVectorizer()
        self.fallback_vectorizer = (
            fallback_vectorizer
            if fallback_vectorizer is not None
            else (TfidfTextVectorizer() if vectorizer is not None else None)
        )

    def analyze(
        self,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
    ) -> CoverageAnalysis:
        subdirections = list(dict.fromkeys(plan.expected_subdirections))
        covered: dict[str, list[str]] = {name: [] for name in subdirections}
        gaps: list[CoverageGap] = []
        if papers and subdirections:
            queries = [f"{plan.normalized_domain} {name}" for name in subdirections]
            documents = [f"{paper.title} {paper.abstract or ''}" for paper in papers]
            try:
                vectors = self.vectorizer.vectorize([*queries, *documents])
            except Exception:
                if self.fallback_vectorizer is None:
                    raise
                vectors = self.fallback_vectorizer.vectorize([*queries, *documents])
            query_vectors = vectors[: len(queries)]
            document_vectors = vectors[len(queries) :]
            for name, query_vector in zip(subdirections, query_vectors, strict=True):
                covered[name] = [
                    paper.paper_id
                    for paper, document_vector in zip(papers, document_vectors, strict=True)
                    if cosine_similarity(query_vector, document_vector)
                    >= self.config.coverage_similarity_threshold
                ]

        for subdirection in subdirections:
            if covered[subdirection]:
                continue
            gaps.append(
                self._gap(
                    plan,
                    subdirection=subdirection,
                    missing_roles=["method"],
                    reason="no selected paper is sufficiently related to this expected subdirection",
                )
            )

        covered_roles = sorted({paper.paper_role for paper in papers})
        for role in self.required_global_roles:
            if role in covered_roles:
                continue
            gaps.append(
                self._gap(
                    plan,
                    subdirection=plan.normalized_domain,
                    missing_roles=[role],
                    reason=f"selected papers do not cover the required {role} role",
                    id_value=f"{plan.normalized_domain}:{role}",
                )
            )
        return CoverageAnalysis(
            gaps=gaps,
            covered_subdirections=covered,
            covered_roles=covered_roles,
        )

    def _gap(
        self,
        plan: DomainResearchPlan,
        *,
        subdirection: str,
        missing_roles: list[PaperRole],
        reason: str,
        id_value: str | None = None,
    ) -> CoverageGap:
        english_context = plan.search_queries[0] if plan.search_queries else plan.normalized_domain
        queries = [
            f'"{english_context}" "{subdirection}" {self.role_query_terms[role]}'
            for role in missing_roles
        ]
        return CoverageGap(
            subdirection_id=stable_id("sub", id_value or subdirection),
            subdirection=subdirection,
            missing_roles=missing_roles,
            reason=reason,
            supplemental_queries=queries,
        )
