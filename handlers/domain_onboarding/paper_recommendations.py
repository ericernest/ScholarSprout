"""Build a user-facing survey-led reading list separately from evidence papers."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import DomainOnboardingConfig
from .schemas import DomainResearchPlan, PaperCandidate, PaperSearchQuery, RankedPaper


@dataclass(slots=True)
class RecommendationBuildResult:
    papers: list[RankedPaper] = field(default_factory=list)
    survey_candidates: int = 0
    selected_surveys: int = 0
    reference_candidates: int = 0
    selected_references: int = 0
    degraded: bool = False
    errors: list[str] = field(default_factory=list)


class SurveyRecommendationPolicy:
    _generic_branch_names = {
        "theoretical foundations and problem formulation",
        "core methods and architectures",
        "evaluation benchmarks and research frontiers",
    }
    _term_stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "into", "is", "of", "on", "or", "our", "paper", "the", "this",
        "to", "using", "via", "we", "with", "approach", "framework", "method",
        "methods", "model", "models", "new", "study", "system", "systems",
    }

    def __init__(self, ranker: Any, config: DomainOnboardingConfig) -> None:
        self.ranker = ranker
        self.config = config

    def queries(self, plan: DomainResearchPlan) -> list[PaperSearchQuery]:
        """Build candidate queries from model-proposed and safe fixed aliases."""

        domain = plan.translated_domain or plan.normalized_domain
        terms = list(dict.fromkeys([domain, *plan.expanded_terms]))
        queries: list[PaperSearchQuery] = []
        for index, term in enumerate(terms, start=1):
            if not self._usable_term(term):
                continue
            queries.append(
                PaperSearchQuery(
                    query=f'"{term.strip()}" survey systematic review taxonomy',
                    role_hint="survey",
                    path_id=f"recommendation-survey-term-{index}",
                    priority=1 if index == 1 else 2,
                )
            )
        for branch in plan.subdirection_plans:
            branch_name = branch.name_en.strip()
            if (
                branch_name.casefold() in self._generic_branch_names
                or not self._usable_term(branch_name)
            ):
                continue
            queries.append(
                PaperSearchQuery(
                    query=f'"{domain}" "{branch_name}" survey review taxonomy',
                    role_hint="survey",
                    path_id=branch.subdirection_id,
                    priority=2,
                )
            )
        deduplicated = {query.query.casefold(): query for query in queries}
        return list(deduplicated.values())[
            : self.config.recommendation_query_candidate_limit
        ]

    def evidence_survey_candidates(
        self,
        candidates: list[PaperCandidate],
    ) -> list[PaperCandidate]:
        """Return independently verifiable surveys from the evidence pool.

        Evidence and recommendations remain separate usages, but a stable paper may
        be promoted to ``both`` after the dedicated recommendation searches have
        failed.  This prevents a query-construction degradation from hiding a
        survey that was already retrieved and can be verified from its metadata.
        """

        surveys: list[PaperCandidate] = []
        seen: set[str] = set()
        for paper in candidates:
            if paper.paper_id in seen or not self._is_survey_candidate(paper):
                continue
            if not (paper.doi or paper.arxiv_id or paper.url.startswith(("http://", "https://"))):
                continue
            if not paper.title.strip() or not (paper.abstract or "").strip():
                continue
            seen.add(paper.paper_id)
            surveys.append(paper.model_copy(deep=True))
        return surveys

    def discovered_queries(
        self,
        plan: DomainResearchPlan,
        evidence_candidates: list[PaperCandidate],
    ) -> tuple[list[PaperSearchQuery], list[str]]:
        terms = self.discover_terms(plan, evidence_candidates)
        queries = [
            PaperSearchQuery(
                query=f'"{term}" survey systematic review taxonomy',
                role_hint="survey",
                path_id=f"recommendation-survey-discovered-{index}",
                priority=1,
            )
            for index, term in enumerate(terms, start=1)
        ]
        return queries, terms

    def discover_terms(
        self,
        plan: DomainResearchPlan,
        candidates: list[PaperCandidate],
    ) -> list[str]:
        """Extract domain-anchored phrases from real titles and abstracts."""

        domain = (plan.translated_domain or plan.normalized_domain).casefold()
        domain_tokens = {
            token
            for token in re.findall(r"[a-z][a-z0-9]+", domain)
            if token not in self._term_stopwords
        }
        if not domain_tokens:
            domain_tokens = set(re.findall(r"[a-z][a-z0-9]+", domain))
        document_frequency: Counter[str] = Counter()
        title_frequency: Counter[str] = Counter()
        for paper in candidates[:30]:
            title = paper.title.casefold().replace("-", " ")
            abstract = (paper.abstract or "").casefold().replace("-", " ")[:1200]
            phrases_in_document: set[str] = set()
            for text, title_weight in ((title, True), (abstract, False)):
                tokens = re.findall(r"[a-z][a-z0-9]+", text)
                for size in range(2, min(5, len(tokens)) + 1):
                    for start in range(0, len(tokens) - size + 1):
                        words = tokens[start : start + size]
                        if words[0] in self._term_stopwords or words[-1] in self._term_stopwords:
                            continue
                        # A Chinese-only fallback plan has no English anchor.  The
                        # surrounding papers have already passed domain ranking, so
                        # repeated title/abstract phrases are a safer bootstrap than
                        # returning no recommendation query at all.
                        if domain_tokens and not domain_tokens.intersection(words):
                            continue
                        if sum(word not in self._term_stopwords for word in words) < 2:
                            continue
                        phrase = " ".join(words)
                        if phrase == domain:
                            continue
                        phrases_in_document.add(phrase)
                        if title_weight:
                            title_frequency[phrase] += 1
            document_frequency.update(phrases_in_document)
        ranked = sorted(
            document_frequency,
            key=lambda phrase: (
                document_frequency[phrase] * 3 + title_frequency[phrase] * 2,
                len(phrase.split()),
                phrase,
            ),
            reverse=True,
        )
        selected: list[str] = []
        for phrase in ranked:
            if any(phrase in existing or existing in phrase for existing in selected):
                continue
            selected.append(phrase)
            if len(selected) >= self.config.recommendation_discovered_term_limit:
                break
        return selected

    def validate_queries(
        self,
        queries: list[PaperSearchQuery],
        candidates: list[PaperCandidate],
        plan: DomainResearchPlan,
    ) -> tuple[list[PaperSearchQuery], list[dict[str, Any]]]:
        unique_candidates = list(
            {paper.paper_id: paper for paper in candidates}.values()
        )
        ranked_candidates = (
            self.ranker.rank(
                unique_candidates,
                plan,
                limit=min(
                    len(unique_candidates),
                    self.config.candidate_paper_limit,
                ),
            ).papers
            if unique_candidates
            else []
        )
        relevance_by_id = {
            paper.paper_id: paper.relevance_score for paper in ranked_candidates
        }
        audits: list[dict[str, Any]] = []
        for query in queries:
            matched = [
                paper for paper in candidates if query.query in paper.matched_queries
            ]
            surveys = [paper for paper in matched if self._is_survey_candidate(paper)]
            relevance = (
                sum(relevance_by_id.get(paper.paper_id, 0.0) for paper in matched)
                / len(matched)
                if matched
                else 0.0
            )
            survey_yield = min(1.0, len(surveys) / 2.0)
            abstract_ratio = (
                sum(bool((paper.abstract or "").strip()) for paper in surveys)
                / len(surveys)
                if surveys
                else 0.0
            )
            current_year = datetime.now(timezone.utc).year
            recent_ratio = (
                sum(
                    bool(
                        paper.year
                        and paper.year
                        >= current_year - self.config.recommendation_recent_year_window
                    )
                    for paper in surveys
                )
                / len(surveys)
                if surveys
                else 0.0
            )
            citation_ratio = (
                sum(paper.citation_count is not None for paper in surveys)
                / len(surveys)
                if surveys
                else 0.0
            )
            source_coverage = min(1.0, len({paper.source for paper in surveys}) / 2.0)
            score = (
                0.40 * relevance
                + 0.25 * survey_yield
                + 0.15 * abstract_ratio
                + 0.15 * recent_ratio
                + 0.05 * source_coverage
            )
            audits.append(
                {
                    "query": query.query,
                    "source": self._query_source(query),
                    "result_count": len(matched),
                    "survey_count": len(surveys),
                    "citation_metadata_ratio": round(citation_ratio, 6),
                    "score": round(score, 6),
                    "selected": False,
                    "reason": (
                        "validated_survey_results"
                        if surveys and score >= self.config.recommendation_min_query_score
                        else "no_verified_survey"
                        if not surveys
                        else "below_query_score_threshold"
                    ),
                }
            )
        eligible = [
            item
            for item in audits
            if item["survey_count"] > 0
            and item["score"] >= self.config.recommendation_min_query_score
        ]
        eligible.sort(
            key=lambda item: (item["score"], item["survey_count"], item["result_count"]),
            reverse=True,
        )
        selected_text = {
            item["query"]
            for item in eligible[: self.config.recommendation_survey_query_limit]
        }
        for item in audits:
            item["selected"] = item["query"] in selected_text
        return [query for query in queries if query.query in selected_text], audits

    @staticmethod
    def candidates_for_queries(
        candidates: list[PaperCandidate],
        queries: list[PaperSearchQuery],
    ) -> list[PaperCandidate]:
        selected = {query.query for query in queries}
        return [
            paper
            for paper in candidates
            if selected.intersection(paper.matched_queries)
        ]

    def select_surveys(
        self,
        candidates: list[PaperCandidate],
        plan: DomainResearchPlan,
        *,
        language: str,
    ) -> tuple[list[RankedPaper], int]:
        if not candidates:
            return [], 0
        ranked = self.ranker.rank(
            candidates,
            plan,
            limit=min(self.config.candidate_paper_limit, max(12, len(candidates))),
        ).papers
        surveys = [paper for paper in ranked if self._is_survey(paper)]
        if not surveys:
            return [], 0
        current_year = datetime.now(timezone.utc).year
        selected: list[RankedPaper] = []
        for paper in surveys:
            recent = bool(
                paper.year
                and paper.year >= current_year - self.config.recommendation_recent_year_window
            )
            category = "recent_survey" if recent else "established_survey"
            reason = self._survey_reason(paper, recent=recent, language=language)
            selected.append(
                paper.model_copy(
                    update={
                        "paper_usage": "recommendation",
                        "recommendation_category": category,
                        "recommendation_reason": reason,
                        "score_context": "survey_recommendation",
                        "reading_priority": "core" if recent else "recommended",
                    }
                )
            )
        selected.sort(key=lambda paper: paper.final_score, reverse=True)
        return selected[: self.config.recommendation_survey_limit], len(surveys)

    def select_references(
        self,
        candidates: list[PaperCandidate],
        plan: DomainResearchPlan,
        *,
        language: str,
    ) -> tuple[list[RankedPaper], int]:
        if not candidates or self.config.recommendation_reference_limit <= 0:
            return [], 0
        ranked = self.ranker.rank(
            candidates,
            plan,
            limit=min(self.config.candidate_paper_limit, max(12, len(candidates))),
        ).papers
        references = [paper for paper in ranked if not self._is_survey(paper)]
        selected: list[RankedPaper] = []
        for paper in references:
            sources = list(dict.fromkeys(paper.survey_source_ids))
            if not sources:
                continue
            reason = self._reference_reason(paper, sources=sources, language=language)
            selected.append(
                paper.model_copy(
                    update={
                        "paper_usage": "recommendation",
                        "recommendation_category": "survey_reference",
                        "recommendation_reason": reason,
                        "score_context": "survey_reference",
                        "reading_priority": "recommended",
                    }
                )
            )
        selected.sort(key=lambda paper: paper.final_score, reverse=True)
        return (
            selected[: self.config.recommendation_reference_limit],
            len(references),
        )

    @staticmethod
    def merge_with_evidence(
        evidence: list[RankedPaper],
        recommendations: list[RankedPaper],
    ) -> list[RankedPaper]:
        merged = [paper.model_copy(deep=True) for paper in evidence]
        indexes = {paper.paper_id: index for index, paper in enumerate(merged)}
        for recommendation in recommendations:
            index = indexes.get(recommendation.paper_id)
            if index is None:
                indexes[recommendation.paper_id] = len(merged)
                merged.append(recommendation.model_copy(deep=True))
                continue
            existing = merged[index]
            merged[index] = existing.model_copy(
                update={
                    "paper_usage": "both",
                    "recommendation_category": recommendation.recommendation_category,
                    "recommendation_reason": recommendation.recommendation_reason,
                    "final_score": recommendation.final_score,
                    "score_version": recommendation.score_version,
                    "score_context": recommendation.score_context,
                    "score_breakdown": recommendation.score_breakdown,
                    "survey_source_ids": list(
                        dict.fromkeys(
                            [*existing.survey_source_ids, *recommendation.survey_source_ids]
                        )
                    ),
                }
            )
        return merged

    @staticmethod
    def _is_survey(paper: RankedPaper) -> bool:
        return SurveyRecommendationPolicy._is_survey_candidate(paper)

    @staticmethod
    def _is_survey_candidate(paper: PaperCandidate) -> bool:
        text = f"{paper.title} {' '.join(paper.publication_types)}".casefold()
        return getattr(paper, "paper_role", "other") == "survey" or any(
            marker in text
            for marker in ("survey", "systematic review", "literature review", "overview")
        )

    @staticmethod
    def _usable_term(term: str) -> bool:
        value = term.strip()
        return bool(
            value
            and len(value) <= 100
            and (
                re.search(r"[A-Za-z]{2}", value)
                or re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", value)
            )
        )

    @staticmethod
    def _query_source(query: PaperSearchQuery) -> str:
        if "discovered" in query.path_id:
            return "retrieval_discovered"
        if "term" in query.path_id:
            return "planner_or_fixed_term"
        return "model_subdirection"

    @staticmethod
    def _survey_reason(paper: RankedPaper, *, recent: bool, language: str) -> str:
        citation = (
            f"引用数 {paper.citation_count}"
            if paper.citation_status == "known"
            else "引用数暂未获取"
        )
        if language == "zh-CN":
            timing = "较新的综述" if recent else "较成熟的综述"
            return f"这是{timing}，适合先建立领域全景；{citation}。"
        timing = "a recent survey" if recent else "an established survey"
        return f"Selected as {timing} for building a field overview; {citation}."

    @staticmethod
    def _reference_reason(
        paper: RankedPaper,
        *,
        sources: list[str],
        language: str,
    ) -> str:
        source_text = "、".join(sources[:2])
        if language == "zh-CN":
            return f"该论文出现在入选综述的参考文献中（{source_text}），可用于追踪代表方法的原始工作。"
        return f"Cited by selected survey {source_text}; useful for tracing an original representative method."
