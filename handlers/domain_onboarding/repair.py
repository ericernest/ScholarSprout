"""按质量问题类型执行确定性修复或一次受约束 LLM 局部修复。"""

from __future__ import annotations

from typing import Protocol

from .config import DomainOnboardingConfig
from .generator import GenerationError, StructuredOnboardingGenerator
from .schemas import (
    ContentQuality,
    DomainOnboardingOutput,
    DomainOnboardingRequest,
    DomainResearchPlan,
    LearnerProfile,
    PaperReference,
    RankedPaper,
    RepairResult,
    SelectedPaper,
)


class OnboardingRepairer(Protocol):
    def repair(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        previous_output: DomainOnboardingOutput,
        quality: ContentQuality,
        allowed_papers: list[RankedPaper],
    ) -> RepairResult: ...


class TargetedRepairer:
    def __init__(self, generator: StructuredOnboardingGenerator, config: DomainOnboardingConfig):
        self.generator = generator
        self.config = config

    def repair(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        previous_output: DomainOnboardingOutput,
        quality: ContentQuality,
        allowed_papers: list[RankedPaper],
    ) -> RepairResult:
        normalized = self._code_repair(previous_output, allowed_papers)
        llm_issue_types = {
            "missing_coverage",
            "weak_development_stage",
            "beginner_mismatch",
            "structure_error",
        }
        selected_issues = [issue for issue in quality.issues if issue.issue_type in llm_issue_types]
        if not selected_issues or self.config.max_content_repairs == 0:
            return RepairResult(output=normalized, action="code_repair")
        try:
            generation = self.generator.repair(
                request,
                profile,
                plan,
                allowed_papers,
                normalized,
                selected_issues,
            )
            return RepairResult(
                output=generation.output,
                action="llm_targeted_repair",
                stats=generation.stats,
            )
        except GenerationError as error:
            return RepairResult(
                output=normalized,
                action="llm_repair_failed",
                stats=error.stats,
            )

    def _code_repair(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
    ) -> DomainOnboardingOutput:
        repaired = output.model_copy(deep=True)
        allowed_map = {paper.paper_id: paper for paper in allowed_papers}
        allowed = set(allowed_map)
        output_ids = list(dict.fromkeys(paper.paper_id for paper in repaired.papers if paper.paper_id in allowed))
        repaired.papers = [SelectedPaper.from_ranked(allowed_map[paper_id]) for paper_id in output_ids]
        for prerequisite in repaired.prerequisites:
            prerequisite.related_paper_ids = self._valid_unique(prerequisite.related_paper_ids, allowed)
        for stage in repaired.development_stages:
            stage.related_paper_ids = self._valid_unique(stage.related_paper_ids, allowed)
            stage.representative_papers = [self._reference(allowed_map[paper_id]) for paper_id in stage.related_paper_ids]
            stage.core_concepts = self._nonempty_unique(stage.core_concepts)
            stage.main_techniques = self._nonempty_unique(stage.main_techniques)
            stage.open_problems = self._nonempty_unique(stage.open_problems)
        for index, step in enumerate(repaired.learning_path, start=1):
            step.step = str(index)
            step.paper_ids = self._valid_unique(step.paper_ids, allowed)
            step.papers = [self._reference(allowed_map[paper_id]) for paper_id in step.paper_ids]
            step.topics = self._nonempty_unique(step.topics)
            step.activities = self._nonempty_unique(step.activities)
            step.completion_criteria = self._nonempty_unique(step.completion_criteria)
        repaired.current_landscape.problems = self._nonempty_unique(repaired.current_landscape.problems)
        repaired.current_landscape.subdirections = self._nonempty_unique(repaired.current_landscape.subdirections)
        return repaired

    @staticmethod
    def _valid_unique(values: list[str], allowed: set[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value in allowed))

    @staticmethod
    def _nonempty_unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @staticmethod
    def _reference(paper: RankedPaper) -> PaperReference:
        return PaperReference(
            paper_id=paper.paper_id,
            title=paper.title,
            authors=paper.authors,
            year=paper.year,
            url=paper.url,
        )
    RepairResult,
