"""分层评估结构、论文真实性、覆盖、发展脉络、路线和目标匹配。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

from .config import DomainOnboardingConfig
from .evidence import ClaimEvidenceValidator
from .schemas import (
    ContentQuality,
    DomainOnboardingOutput,
    QualityGateResult,
    QualityIssue,
    RankedPaper,
)


class OnboardingQualityEvaluator(Protocol):
    def evaluate(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
    ) -> ContentQuality: ...


def _coverage(actual: int, target: int) -> float:
    return min(1.0, actual / max(1, target))


def _average(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


class CompositeQualityEvaluator:
    def __init__(self, config: DomainOnboardingConfig):
        self.config = config
        self.policy = config.to_policy()
        self.evidence_validator = ClaimEvidenceValidator(config)

    def evaluate(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
    ) -> ContentQuality:
        issues: list[QualityIssue] = []
        evidence = self.evidence_validator.validate(output, allowed_papers)
        issues.extend(evidence.issues)
        dimensions = {
            "structure": self.evaluate_structure_quality(output, issues),
            "paper_validity": self.evaluate_paper_validity(output, allowed_papers, issues),
            "evidence_grounding": evidence.score,
            "topic_coverage": self.evaluate_topic_coverage(output, issues),
            "development_coherence": self.evaluate_development_coherence(output, issues),
            "learning_path": self.evaluate_learning_path_quality(output, issues),
            "goal_alignment": self.evaluate_goal_alignment(output, issues),
        }
        hard_gates = self._hard_gate_results(issues)
        passed_hard_gates = all(gate.status == "passed" for gate in hard_gates)
        score = sum(
            dimensions[name] * weight
            for name, weight in self.policy.dimension_weights.items()
        )
        return ContentQuality(
            score=round(max(0.0, min(1.0, score)), 6),
            threshold=self.policy.quality_threshold,
            passed_hard_gates=passed_hard_gates,
            dimensions={key: round(value, 6) for key, value in dimensions.items()},
            issues=issues,
            hard_gates=hard_gates,
            policy_version=self.policy.policy_version,
            policy_fingerprint=self.policy.fingerprint,
        )

    def _hard_gate_results(self, issues: list[QualityIssue]) -> list[QualityGateResult]:
        results = []
        gated_dimensions = {
            dimension
            for dimensions in self.policy.hard_gate_dimensions.values()
            for dimension in dimensions
        }
        severities = set(self.policy.hard_gate_severities)
        for issue in issues:
            issue.hard_gate = bool(
                issue.dimension in gated_dimensions and issue.severity in severities
            )
        for gate, dimensions in self.policy.hard_gate_dimensions.items():
            issue_ids = [
                str(issue.issue_id)
                for issue in issues
                if issue.hard_gate and issue.dimension in set(dimensions)
            ]
            results.append(
                QualityGateResult(
                    gate=gate,
                    status="failed" if issue_ids else "passed",
                    issue_ids=issue_ids,
                )
            )
        return results

    def evaluate_structure_quality(
        self,
        output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> float:
        checks = [
            bool(output.domain.strip()),
            len(output.text.strip()) >= 40,
            bool(output.prerequisites),
            len(output.development_stages) >= self.config.min_development_stages,
            bool(output.current_landscape.problems),
            len(output.current_landscape.subdirections) >= self.config.min_subdirections,
            len(output.learning_path) >= self.config.min_learning_steps,
            bool(output.papers),
        ]
        if not all(checks):
            issues.append(
                QualityIssue(
                    issue_type="structure_error",
                    severity="error",
                    target_path="$",
                    message="必需模块、最小数量或摘要长度不满足要求。",
                    recommended_action="补齐缺失模块并保持合法 JSON 结构。",
                )
            )
        return sum(checks) / len(checks)

    def evaluate_paper_validity(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
        issues: list[QualityIssue],
    ) -> float:
        allowed = {paper.paper_id: paper for paper in allowed_papers}
        output_ids = {paper.paper_id for paper in output.papers}
        referenced = set(self._all_reference_ids(output))
        invalid = sorted((output_ids | referenced) - set(allowed))
        metadata_mismatches: list[str] = []
        for paper in output.papers:
            source = allowed.get(paper.paper_id)
            if source is None:
                continue
            if (paper.title, paper.year, paper.url) != (source.title, source.year, source.url):
                metadata_mismatches.append(paper.paper_id)
        if invalid or metadata_mismatches:
            issues.append(
                QualityIssue(
                    issue_type="invalid_paper",
                    severity="critical",
                    target_path="papers",
                    message=f"发现非法论文 ID {invalid} 或被修改的元数据 {metadata_mismatches}。",
                    recommended_action="仅保留检索候选集合中的原始论文实体。",
                )
            )
        if not output.papers:
            return 0.0
        valid_count = len(output_ids & set(allowed)) - len(metadata_mismatches)
        return max(0.0, valid_count / len(output.papers))

    def evaluate_topic_coverage(
        self,
        output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> float:
        values = [
            _coverage(len(output.prerequisites), 3),
            _coverage(len(output.development_stages), self.config.min_development_stages),
            _coverage(len(output.current_landscape.problems), 3),
            _coverage(len(output.current_landscape.subdirections), self.config.min_subdirections),
        ]
        score = _average(values)
        if score < 1.0:
            issues.append(
                QualityIssue(
                    issue_type="missing_coverage",
                    severity="warning",
                    target_path="current_landscape",
                    message="领域分支、问题、前置知识或发展阶段覆盖不足。",
                    recommended_action="围绕缺失子方向补充检索并局部生成。",
                )
            )
        return score

    def evaluate_development_coherence(
        self,
        output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> float:
        stage_scores = []
        for index, stage in enumerate(output.development_stages):
            checks = [
                bool(stage.summary.strip()),
                bool(stage.motivation.strip()),
                bool(stage.related_paper_ids),
                bool(stage.core_concepts),
                bool(stage.main_techniques),
                bool(stage.open_problems),
            ]
            stage_scores.append(sum(checks) / len(checks))
            if not all(checks):
                issues.append(
                    QualityIssue(
                        issue_type="weak_development_stage",
                        severity="warning",
                        target_path=f"development_stages[{index}]",
                        message="发展阶段缺少动机、论文、概念、技术或开放问题。",
                        recommended_action="只补充该阶段缺失字段。",
                    )
                )
        return _average(stage_scores)

    def evaluate_learning_path_quality(
        self,
        output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> float:
        expected = [str(index) for index in range(1, len(output.learning_path) + 1)]
        actual = [step.step for step in output.learning_path]
        sequence_ok = actual == expected
        item_score = _average(
            (
                sum(
                    [
                        bool(step.goal.strip()),
                        bool(step.topics),
                        bool(step.activities),
                        bool(step.completion_criteria),
                        bool(step.expected_outcome.strip()),
                    ]
                )
                / 5
                for step in output.learning_path
            )
        )
        score = 0.25 * float(sequence_ok) + 0.75 * item_score
        if score < 1.0:
            issues.append(
                QualityIssue(
                    issue_type="route_conflict",
                    severity="error" if not sequence_ok else "warning",
                    target_path="learning_path",
                    message="学习步骤不连续或缺少活动、目标和完成标准。",
                    recommended_action="按基础到实验再到前沿的固定阶段重新编号并补齐。",
                )
            )
        return score

    def evaluate_goal_alignment(
        self,
        output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> float:
        profile = output.learner_profile
        path_text = " ".join(
            [
                *(step.goal for step in output.learning_path),
                *(activity for step in output.learning_path for activity in step.activities),
            ]
        )
        indicators = [
            term for term in ("实验", "复现", "论文", "阅读", "理论", "方法", "项目", "研究", "选题", "代码", "基线")
            if term in profile.goal
        ]
        lexical = (
            sum(1 for term in indicators if term in path_text) / len(indicators)
            if indicators else 1.0
        )
        preference_ok = True
        if profile.preference == "experiment_first":
            preference_ok = bool(re.search(r"实验|复现|基线", path_text))
        elif profile.preference == "theory_first":
            preference_ok = bool(re.search(r"理论|推导|原理|概念", path_text))
        score = 0.7 * lexical + 0.3 * float(preference_ok)
        if score < 0.65:
            issues.append(
                QualityIssue(
                    issue_type="beginner_mismatch",
                    severity="warning",
                    target_path="learning_path",
                    message="学习路径与用户目标或偏好匹配不足。",
                    recommended_action="按画像局部改写活动和完成标准。",
                )
            )
        return score

    @staticmethod
    def _all_reference_ids(output: DomainOnboardingOutput) -> Iterable[str]:
        for item in output.prerequisites:
            yield from item.related_paper_ids
        for stage in output.development_stages:
            yield from stage.related_paper_ids
            yield from (paper.paper_id for paper in stage.representative_papers)
        for step in output.learning_path:
            yield from step.paper_ids
            yield from (paper.paper_id for paper in step.papers)
        for claim in output.evidence_claims:
            yield from claim.supporting_paper_ids


def critical_dimensions_not_regressed(
    first: ContentQuality,
    retry: ContentQuality,
) -> bool:
    for name in ("structure", "paper_validity", "evidence_grounding", "learning_path"):
        if retry.dimensions.get(name, 0.0) + 1e-9 < first.dimensions.get(name, 0.0):
            return False
    return True
