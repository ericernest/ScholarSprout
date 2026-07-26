"""按质量问题类型执行确定性修复或一次受约束 LLM 局部修复。"""

from __future__ import annotations

from typing import Protocol

from .config import DomainOnboardingConfig
from .generator import GenerationError, StructuredOnboardingGenerator
from .repair_planning import RepairPlanner
from .schemas import (
    ContentQuality,
    DomainOnboardingOutput,
    DomainOnboardingRequest,
    DomainResearchPlan,
    LearnerProfile,
    PaperReference,
    RankedPaper,
    RepairResult,
    RepairRecord,
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
    def __init__(
        self,
        generator: StructuredOnboardingGenerator,
        config: DomainOnboardingConfig,
        planner: RepairPlanner | None = None,
    ):
        self.generator = generator
        self.config = config
        self.planner = planner or RepairPlanner()

    def repair(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        previous_output: DomainOnboardingOutput,
        quality: ContentQuality,
        allowed_papers: list[RankedPaper],
    ) -> RepairResult:
        plan = self.planner.plan(
            quality,
            max_content_repairs=self.config.max_content_repairs,
        )
        actions = [action.model_copy(deep=True) for action in plan.actions]
        normalized = self._code_repair(previous_output, allowed_papers)
        code_action = next(
            (action for action in actions if action.action_type == "code"),
            None,
        )
        if code_action is not None:
            code_action.status = "applied"
        llm_action = next(
            (action for action in actions if action.action_type == "llm"),
            None,
        )
        selected_ids = set(llm_action.issue_ids if llm_action else [])
        selected_issues = [
            issue for issue in quality.issues if str(issue.issue_id) in selected_ids
        ]
        record = RepairRecord(triggered=True, actions=actions)
        if not selected_issues or self.config.max_content_repairs == 0:
            return RepairResult(
                output=normalized,
                action="code_repair",
                record=record,
            )
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
                record=self._with_action_status(record, "llm", "applied"),
            )
        except GenerationError as error:
            return RepairResult(
                output=normalized,
                action="llm_repair_failed",
                stats=error.stats,
                record=self._with_action_status(
                    record,
                    "llm",
                    "failed",
                    error=str(error),
                ),
            )

    @staticmethod
    def _with_action_status(
        record: RepairRecord,
        action_type: str,
        status: str,
        *,
        error: str | None = None,
    ) -> RepairRecord:
        updated = record.model_copy(deep=True)
        action = next(
            item for item in updated.actions if item.action_type == action_type
        )
        action.status = status
        action.error = error
        return updated

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
        seen_claims: set[str] = set()
        normalized_claims = []
        for claim in repaired.evidence_claims:
            claim.supporting_paper_ids = self._valid_unique(
                claim.supporting_paper_ids,
                allowed,
            )
            if claim.claim_id in seen_claims:
                continue
            seen_claims.add(str(claim.claim_id))
            normalized_claims.append(claim)
        repaired.evidence_claims = normalized_claims
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
