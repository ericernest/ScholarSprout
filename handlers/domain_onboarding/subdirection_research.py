"""Deterministic contracts for independently researching planned subdirections."""

from __future__ import annotations

from typing import Any

from .config import DomainOnboardingConfig
from .schemas import (
    DomainResearchPlan,
    PaperCandidate,
    PaperSearchQuery,
    RankedPaper,
    RankingResult,
    ResearchPerspective,
    SubdirectionEvidenceBundle,
    SubdirectionResearchPlan,
)


class SubdirectionPaperRanker:
    """Apply branch role coverage without creating a second paper score."""

    def __init__(self, base_ranker: Any, config: DomainOnboardingConfig) -> None:
        self.base_ranker = base_ranker
        self.config = config

    def rank(
        self,
        papers: list[PaperCandidate],
        plan: DomainResearchPlan,
        branch: SubdirectionResearchPlan,
        *,
        limit: int,
    ) -> RankingResult:
        pool_limit = min(
            self.config.selected_paper_limit,
            max(limit, limit * 3),
        )
        base = self.base_ranker.rank(papers, plan, limit=pool_limit)
        if not base.papers:
            base.stats.ranking_strategy = "subdirection_grounded_rank"
            return base

        ranked = sorted(base.papers, key=lambda paper: paper.final_score, reverse=True)
        selected: list[RankedPaper] = []
        for roles in ({"method"}, {"survey", "evaluation"}):
            candidate = next(
                (
                    paper
                    for paper in ranked
                    if paper.paper_role in roles and paper not in selected
                ),
                None,
            )
            if candidate is not None and len(selected) < limit:
                selected.append(candidate)
        for paper in ranked:
            if len(selected) >= limit:
                break
            if paper not in selected:
                selected.append(paper)
        base.papers = selected
        selected.sort(key=lambda paper: paper.final_score, reverse=True)
        base.stats.ranking_strategy = "subdirection_unified_score_role_gate"
        base.stats.selected_role_counts = {
            role: sum(paper.paper_role == role for paper in selected)
            for role in sorted({paper.paper_role for paper in selected})
        }
        return base

class SubdirectionResearchPolicy:
    def __init__(self, config: DomainOnboardingConfig) -> None:
        self.config = config

    def direction_plan(
        self,
        domain_plan: DomainResearchPlan,
        branch: SubdirectionResearchPlan,
        queries: list[PaperSearchQuery],
    ) -> DomainResearchPlan:
        english_domain = domain_plan.translated_domain or domain_plan.normalized_domain
        perspectives = [
            ResearchPerspective(
                path_id=f"{branch.subdirection_id}-{index}",
                name=f"{branch.name_en} {query.role_hint}",
                description=branch.scope,
                questions=list(branch.research_questions) or [branch.scope],
                search_queries=[query.query],
            )
            for index, query in enumerate(queries, start=1)
        ]
        while len(perspectives) < 3:
            index = len(perspectives) + 1
            perspectives.append(
                ResearchPerspective(
                    path_id=f"{branch.subdirection_id}-scope-{index}",
                    name=branch.name_en,
                    description=branch.scope,
                    questions=list(branch.research_questions) or [branch.scope],
                    search_queries=[
                        f'"{english_domain}" "{branch.name_en}" research'
                    ],
                )
            )
        return DomainResearchPlan(
            normalized_domain=domain_plan.normalized_domain,
            translated_domain=english_domain,
            expanded_terms=list(
                dict.fromkeys(
                    [
                        *domain_plan.expanded_terms,
                        branch.name_en,
                        *branch.include_terms,
                    ]
                )
            ),
            perspectives=perspectives[:3],
            search_queries=[query.query for query in queries],
            paper_queries=queries,
            expected_subdirections=[
                branch.name_zh,
                branch.name_en,
                branch.scope,
            ],
            subdirection_plans=[branch],
        )

    @staticmethod
    def exclude_out_of_scope(
        papers: list[PaperCandidate],
        branch: SubdirectionResearchPlan,
    ) -> list[PaperCandidate]:
        exclusions = [term.casefold() for term in branch.exclude_terms if term.strip()]
        if not exclusions:
            return papers
        return [
            paper
            for paper in papers
            if not any(
                term in f"{paper.title} {paper.abstract or ''}".casefold()
                for term in exclusions
            )
        ]

    def assess(
        self,
        branch: SubdirectionResearchPlan,
        papers: list[RankedPaper],
        *,
        query_count: int,
        supplemental_query_count: int,
    ) -> SubdirectionEvidenceBundle:
        abstract_count = sum(bool((paper.abstract or "").strip()) for paper in papers)
        roles = sorted({paper.paper_role for paper in papers})
        warnings: list[str] = []
        if len(papers) < self.config.subdirection_min_papers:
            warnings.append("insufficient_paper_count")
        if abstract_count < self.config.subdirection_min_abstract_papers:
            warnings.append("insufficient_abstract_count")
        if "method" not in roles:
            warnings.append("missing_method_paper")
        if not {"survey", "evaluation"}.intersection(roles):
            warnings.append("missing_survey_or_evaluation_paper")
        return SubdirectionEvidenceBundle(
            subdirection_id=branch.subdirection_id,
            papers=papers,
            query_count=query_count,
            supplemental_query_count=supplemental_query_count,
            abstract_ready_count=abstract_count,
            covered_roles=roles,
            status="sufficient" if not warnings else "limited",
            warnings=warnings,
        )

    @staticmethod
    def supplemental_query(
        domain_plan: DomainResearchPlan,
        branch: SubdirectionResearchPlan,
        bundle: SubdirectionEvidenceBundle,
    ) -> PaperSearchQuery:
        english_domain = domain_plan.translated_domain or domain_plan.normalized_domain
        if "missing_method_paper" in bundle.warnings:
            role = "method"
            terms = "methods framework architecture"
        elif "missing_survey_or_evaluation_paper" in bundle.warnings:
            role = "evaluation"
            terms = "survey benchmark evaluation"
        else:
            role = "frontier"
            terms = "recent advances state of the art"
        return PaperSearchQuery(
            query=f'"{english_domain}" "{branch.name_en}" {terms}',
            role_hint=role,
            path_id=branch.subdirection_id,
            priority=1,
        )

    def merge(
        self,
        global_ranked: list[RankedPaper],
        bundles: list[SubdirectionEvidenceBundle],
    ) -> list[RankedPaper]:
        selected: list[RankedPaper] = []
        seen: set[str] = set()

        def add(paper: RankedPaper) -> None:
            if (
                len(selected) >= self.config.selected_paper_limit
                or paper.paper_id in seen
            ):
                return
            selected.append(paper)
            seen.add(paper.paper_id)

        for paper in global_ranked:
            if paper.is_canonical or paper.reading_priority == "core":
                add(paper)
        max_bundle_size = max((len(bundle.papers) for bundle in bundles), default=0)
        for paper_index in range(max_bundle_size):
            for bundle in bundles:
                if paper_index < len(bundle.papers):
                    add(bundle.papers[paper_index])
        for paper in global_ranked:
            add(paper)
        return selected
