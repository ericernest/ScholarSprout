"""执行受候选论文和质量问题约束的 LLM 局部修复。"""

from __future__ import annotations

from .generator import StructuredOnboardingGenerator
from .schemas import (
    DomainOnboardingOutput,
    DomainOnboardingRequest,
    DomainResearchPlan,
    GenerationResult,
    LearnerProfile,
    QualityIssue,
    RankedPaper,
)


class LLMRepairExecutor:
    def __init__(self, generator: StructuredOnboardingGenerator):
        self.generator = generator

    def execute(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        allowed_papers: list[RankedPaper],
        previous_output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> GenerationResult:
        return self.generator.repair(
            request,
            profile,
            plan,
            allowed_papers,
            previous_output,
            issues,
        )
