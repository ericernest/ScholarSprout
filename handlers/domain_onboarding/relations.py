"""Resolve navigable onboarding links from explicit IDs and paper evidence."""

from __future__ import annotations

from .schemas import (
    CurrentLandscape,
    DevelopmentStage,
    LandscapeProblem,
    RankedPaper,
    SubdirectionDetail,
)
from .text_similarity import (
    MultilingualEvidenceTextVectorizer,
    TextVectorizer,
    cosine_similarity,
)


class SemanticRelationResolver:
    """Normalize bidirectional links without inventing unsupported edges."""

    def __init__(
        self,
        vectorizer: TextVectorizer | None = None,
        *,
        semantic_threshold: float = 0.08,
    ) -> None:
        self.vectorizer = vectorizer or MultilingualEvidenceTextVectorizer()
        self.semantic_threshold = semantic_threshold

    def resolve(
        self,
        stages: list[DevelopmentStage],
        landscape: CurrentLandscape,
        papers: list[RankedPaper],
    ) -> CurrentLandscape:
        paper_text = {
            paper.paper_id: " ".join([paper.title, paper.abstract or ""])
            for paper in papers
        }
        stage_order = {
            str(stage.stage_id): stage.sequence
            for stage in stages
            if stage.stage_id
        }
        valid_stage_ids = set(stage_order)
        stage_by_paper: dict[str, set[str]] = {}
        for stage in stages:
            if not stage.stage_id:
                continue
            for paper_id in stage.related_paper_ids:
                stage_by_paper.setdefault(paper_id, set()).add(str(stage.stage_id))

        for problem in landscape.problem_details:
            inferred = self._paper_stages(problem.related_paper_ids, stage_by_paper)
            explicit = set(problem.related_stage_ids) & valid_stage_ids
            combined = explicit | inferred
            problem.related_stage_ids = self._ordered(combined, stage_order)
            if problem.emerged_in_stage_id not in combined:
                problem.emerged_in_stage_id = (
                    problem.related_stage_ids[0] if problem.related_stage_ids else None
                )
            affected = (set(problem.affected_stage_ids) & valid_stage_ids) | inferred
            if problem.emerged_in_stage_id:
                affected.add(problem.emerged_in_stage_id)
            problem.affected_stage_ids = self._ordered(affected, stage_order)
            problem.relation_status = self._relation_status(explicit, inferred)

        for direction in landscape.subdirection_details:
            inferred = self._paper_stages(direction.related_paper_ids, stage_by_paper)
            explicit = set(direction.related_stage_ids) & valid_stage_ids
            combined = explicit | inferred
            direction.related_stage_ids = self._ordered(combined, stage_order)
            if direction.emerged_in_stage_id not in combined:
                direction.emerged_in_stage_id = (
                    direction.related_stage_ids[0]
                    if direction.related_stage_ids
                    else None
                )
            direction.relation_status = self._relation_status(explicit, inferred)

        self._resolve_problem_direction_links(
            landscape.problem_details,
            landscape.subdirection_details,
            paper_text,
        )
        self._resolve_breakthrough_problem_links(stages, landscape.problem_details)
        return landscape

    def _resolve_problem_direction_links(
        self,
        problems: list[LandscapeProblem],
        directions: list[SubdirectionDetail],
        paper_text: dict[str, str],
    ) -> None:
        problem_by_id = {
            str(problem.problem_id): problem
            for problem in problems
            if problem.problem_id
        }
        direction_by_id = {
            str(direction.subdirection_id): direction
            for direction in directions
            if direction.subdirection_id
        }
        valid_problem_ids = set(problem_by_id)
        valid_direction_ids = set(direction_by_id)

        explicit_by_direction: dict[str, list[str]] = {}
        for direction in directions:
            direction_id = str(direction.subdirection_id or "")
            from_direction = [
                problem_id
                for problem_id in direction.addresses_problem_ids
                if problem_id in valid_problem_ids
            ]
            from_problem = [
                str(problem.problem_id)
                for problem in problems
                if problem.problem_id
                and direction_id in problem.related_subdirection_ids
            ]
            explicit = list(dict.fromkeys([*from_direction, *from_problem]))
            explicit_by_direction[direction_id] = explicit
            direction.addresses_problem_ids = explicit
            if direction.addresses_problem_ids:
                direction.relation_status = "explicit"
                continue
            best = self._best_problem(direction, problems, paper_text)
            if best is not None and best.problem_id:
                direction.addresses_problem_ids = [str(best.problem_id)]
                direction.relation_status = "semantic_inferred"

        for problem in problems:
            explicit = [
                direction_id
                for direction_id in problem.related_subdirection_ids
                if direction_id in valid_direction_ids
            ]
            reverse = [
                str(direction.subdirection_id)
                for direction in directions
                if direction.subdirection_id
                and str(problem.problem_id) in direction.addresses_problem_ids
            ]
            problem.related_subdirection_ids = list(
                dict.fromkeys([*explicit, *reverse])
            )
            if explicit:
                problem.relation_status = "explicit"
            elif reverse and problem.relation_status == "unresolved":
                problem.relation_status = "semantic_inferred"

    def _best_problem(
        self,
        direction: SubdirectionDetail,
        problems: list[LandscapeProblem],
        paper_text: dict[str, str],
    ) -> LandscapeProblem | None:
        if not problems:
            return None
        direction_text = " ".join(
            [
                direction.name,
                direction.description,
                direction.why_it_matters,
                *direction.typical_tasks,
                *direction.prerequisites,
                *(technique.name for technique in direction.common_techniques),
                *(technique.explanation for technique in direction.common_techniques),
                *direction.datasets_and_benchmarks,
                *direction.evaluation_metrics,
                direction.starter_project,
                *direction.research_workflow,
                *direction.research_questions,
                *(paper_text.get(paper_id, "") for paper_id in direction.related_paper_ids),
            ]
        )
        problem_texts = [
            " ".join(
                [
                    problem.name,
                    problem.description,
                    *(paper_text.get(paper_id, "") for paper_id in problem.related_paper_ids),
                ]
            )
            for problem in problems
        ]
        vectors = self.vectorizer.vectorize([direction_text, *problem_texts])
        best: tuple[float, LandscapeProblem] | None = None
        direction_papers = set(direction.related_paper_ids)
        direction_stages = set(direction.related_stage_ids)
        for index, problem in enumerate(problems, start=1):
            text_score = cosine_similarity(vectors[0], vectors[index])
            paper_overlap = self._overlap(
                direction_papers, set(problem.related_paper_ids)
            )
            stage_overlap = self._overlap(
                direction_stages, set(problem.related_stage_ids)
            )
            score = 0.55 * text_score + 0.30 * paper_overlap + 0.15 * stage_overlap
            grounded = paper_overlap > 0 or stage_overlap > 0
            if not grounded and text_score < self.semantic_threshold:
                continue
            if best is None or score > best[0]:
                best = (score, problem)
        return best[1] if best else None

    def _resolve_breakthrough_problem_links(
        self,
        stages: list[DevelopmentStage],
        problems: list[LandscapeProblem],
    ) -> None:
        for stage in stages:
            stage_id = str(stage.stage_id or "")
            related = [
                problem
                for problem in problems
                if stage_id
                and (
                    problem.emerged_in_stage_id == stage_id
                    or stage_id in problem.related_stage_ids
                )
            ]
            related_ids = [
                str(problem.problem_id) for problem in related if problem.problem_id
            ]
            stage.related_problem_ids = list(dict.fromkeys(related_ids))
            for breakthrough in stage.breakthroughs:
                valid_explicit = [
                    problem_id
                    for problem_id in breakthrough.limitation_problem_ids
                    if problem_id in related_ids
                ]
                breakthrough.limitation_problem_ids = (
                    list(dict.fromkeys(valid_explicit))
                    if valid_explicit
                    else related_ids
                )
                breakthrough.relation_status = (
                    "explicit"
                    if valid_explicit
                    else "paper_inferred"
                    if related_ids
                    else "unresolved"
                )

    @staticmethod
    def _paper_stages(
        paper_ids: list[str], stage_by_paper: dict[str, set[str]]
    ) -> set[str]:
        return {
            stage_id
            for paper_id in paper_ids
            for stage_id in stage_by_paper.get(paper_id, set())
        }

    @staticmethod
    def _ordered(values: set[str], order: dict[str, int]) -> list[str]:
        return sorted(values, key=lambda item: (order.get(item, 10**9), item))

    @staticmethod
    def _relation_status(explicit: set[str], inferred: set[str]) -> str:
        if inferred and not explicit:
            return "paper_inferred"
        if explicit:
            return "explicit"
        return "unresolved"

    @staticmethod
    def _overlap(left: set[str], right: set[str]) -> float:
        union = left | right
        return len(left & right) / len(union) if union else 0.0
