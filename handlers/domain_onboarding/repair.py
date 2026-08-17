"""按质量问题类型执行确定性修复或一次受约束 LLM 局部修复。"""

from __future__ import annotations

from collections.abc import Callable

from typing import Protocol

from .config import DomainOnboardingConfig
from .generator import GenerationError, StructuredOnboardingGenerator
from .repair_code import CodeRepairExecutor
from .repair_diff import (
    apply_targeted_changes,
    changed_output_paths,
    fingerprint_output,
    paths_outside_targets,
)
from .repair_llm import LLMRepairExecutor
from .repair_planning import RepairPlanner
from .schemas import (
    ContentQuality,
    DomainOnboardingOutput,
    DomainOnboardingRequest,
    DomainResearchPlan,
    LearnerProfile,
    RankedPaper,
    RepairResult,
    RepairRecord,
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
        on_delta: Callable[[str, str], None] | None = None,
    ) -> RepairResult: ...


class TargetedRepairer:
    def __init__(
        self,
        generator: StructuredOnboardingGenerator,
        config: DomainOnboardingConfig,
        planner: RepairPlanner | None = None,
        code_executor: CodeRepairExecutor | None = None,
        llm_executor: LLMRepairExecutor | None = None,
    ):
        self.generator = generator
        self.config = config
        self.policy = config.to_policy()
        self.planner = planner or RepairPlanner(
            self.policy.llm_repair_issue_types,
            max_llm_issues=self.config.repair_max_llm_issues,
        )
        self.code_executor = code_executor or CodeRepairExecutor()
        self.llm_executor = llm_executor or LLMRepairExecutor(generator)

    def repair(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        previous_output: DomainOnboardingOutput,
        quality: ContentQuality,
        allowed_papers: list[RankedPaper],
        on_delta: Callable[[str, str], None] | None = None,
    ) -> RepairResult:
        repair_plan = self.planner.plan(
            quality,
            max_content_repairs=self.config.max_content_repairs,
        )
        actions = [action.model_copy(deep=True) for action in repair_plan.actions]
        normalized = self.code_executor.execute(previous_output, allowed_papers)
        code_action = next(
            (action for action in actions if action.action_type == "code"),
            None,
        )
        if code_action is not None:
            code_action.before_fingerprint = fingerprint_output(previous_output)
            code_action.after_fingerprint = fingerprint_output(normalized)
            code_action.changed_paths = changed_output_paths(previous_output, normalized)
            code_action.status = "applied" if code_action.changed_paths else "skipped"
        llm_action = next(
            (action for action in actions if action.action_type == "llm"),
            None,
        )
        selected_ids = set(llm_action.issue_ids if llm_action else [])
        selected_issues = [
            issue for issue in quality.issues if str(issue.issue_id) in selected_ids
        ]
        record = RepairRecord(
            triggered=True,
            actions=actions,
            policy_version=self.policy.policy_version,
            policy_fingerprint=self.policy.fingerprint,
        )
        if not selected_issues or self.config.max_content_repairs == 0:
            return RepairResult(
                output=normalized,
                action="code_repair",
                record=record,
            )
        try:
            generation = self.llm_executor.execute(
                request,
                profile,
                plan,
                allowed_papers,
                normalized,
                selected_issues,
                on_delta,
            )
            full_candidate = self.code_executor.execute(generation.output, allowed_papers)
            repair_targets = self._repair_scope_targets(
                llm_action.target_paths if llm_action else []
            )
            if llm_action is not None:
                llm_action.target_paths = repair_targets
            candidate = apply_targeted_changes(
                normalized,
                full_candidate,
                repair_targets,
            )
            changed_paths = changed_output_paths(normalized, candidate)
            outside_targets = paths_outside_targets(
                changed_paths,
                repair_targets,
            )
            if outside_targets:
                message = f"repair changed fields outside target paths: {outside_targets}"
                return RepairResult(
                    output=normalized,
                    action="llm_repair_failed",
                    stats=generation.stats,
                    record=self._with_action_status(
                        record,
                        "llm",
                        "failed",
                        before=normalized,
                        after=candidate,
                        changed_paths=changed_paths,
                        error=message,
                    ),
                )
            return RepairResult(
                output=candidate,
                action="llm_targeted_repair",
                stats=generation.stats,
                record=self._with_action_status(
                    record,
                    "llm",
                    "applied" if changed_paths else "skipped",
                    before=normalized,
                    after=candidate,
                    changed_paths=changed_paths,
                ),
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
                    before=normalized,
                    error=str(error),
                ),
            )

    @staticmethod
    def _repair_scope_targets(target_paths: list[str]) -> list[str]:
        expanded = []
        for path in target_paths:
            if path.startswith("evidence_claims["):
                expanded.append(path.split("]", 1)[0] + "]")
            else:
                expanded.append(path)
        return list(dict.fromkeys(expanded))

    @staticmethod
    def _with_action_status(
        record: RepairRecord,
        action_type: str,
        status: str,
        *,
        before: DomainOnboardingOutput | None = None,
        after: DomainOnboardingOutput | None = None,
        changed_paths: list[str] | None = None,
        error: str | None = None,
    ) -> RepairRecord:
        updated = record.model_copy(deep=True)
        action = next(
            item for item in updated.actions if item.action_type == action_type
        )
        action.status = status
        action.changed_paths = list(changed_paths or [])
        action.before_fingerprint = fingerprint_output(before) if before else None
        action.after_fingerprint = fingerprint_output(after) if after else None
        action.error = error
        return updated
    RepairResult,
