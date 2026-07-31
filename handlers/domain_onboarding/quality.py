"""分层评估结构、论文真实性、覆盖、发展脉络、路线和目标匹配。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

from .canonical_papers import CanonicalPaperRegistry
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
    def __init__(
        self,
        config: DomainOnboardingConfig,
        evidence_vectorizer: object | None = None,
    ):
        self.config = config
        self.policy = config.to_policy()
        self.evidence_validator = ClaimEvidenceValidator(config, evidence_vectorizer)
        self.canonical_registry = CanonicalPaperRegistry()

    def evaluate(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
    ) -> ContentQuality:
        issues: list[QualityIssue] = []
        fallback_sections = output.reproducibility.get(
            "generation_fallback_sections", []
        )
        if isinstance(fallback_sections, list) and fallback_sections:
            issues.append(
                QualityIssue(
                    issue_type="generation_fallback",
                    severity="warning",
                    target_path="reproducibility.generation_fallback_sections",
                    message=(
                        "以下内容段未使用模型有效输出，已基于真实论文与确定性规则降级生成："
                        f"{fallback_sections}。"
                    ),
                    recommended_action=(
                        "前端应显示降级提示；如需更强语义内容，请重试模型生成或更换可用模型。"
                    ),
                )
            )
        evidence = self.evidence_validator.validate(output, allowed_papers)
        issues.extend(evidence.issues)
        dimensions = {
            "structure": self.evaluate_structure_quality(output, issues),
            "paper_validity": self.evaluate_paper_validity(output, allowed_papers, issues),
            "paper_relevance": self.evaluate_paper_relevance(output, allowed_papers, issues),
            "evidence_grounding": evidence.score,
            "topic_coverage": self.evaluate_topic_coverage(output, issues),
            "development_coherence": self.evaluate_development_coherence(output, issues),
            "learning_path": self.evaluate_learning_path_quality(output, issues),
            "goal_alignment": self.evaluate_goal_alignment(output, issues),
            "language_alignment": self.evaluate_language_alignment(output, issues),
        }
        self._ensure_gate_score_issues(dimensions, issues)
        hard_gates = self._hard_gate_results(dimensions, issues)
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
            evidence_validation_modes=evidence.validation_modes,
        )

    def _ensure_gate_score_issues(
        self,
        dimension_scores: dict[str, float],
        issues: list[QualityIssue],
    ) -> None:
        """Make numeric gate failures actionable by the repair planner."""
        existing_hard_dimensions = {
            issue.dimension
            for issue in issues
            if issue.severity in set(self.policy.hard_gate_severities)
        }
        relevance_threshold = self.policy.hard_gate_min_scores["paper_relevance"]
        if (
            dimension_scores["paper_relevance"] < relevance_threshold
            and "paper_relevance" not in existing_hard_dimensions
        ):
            issues.append(
                QualityIssue(
                    issue_type="low_paper_relevance",
                    severity="error",
                    target_path="papers",
                    message=(
                        "论文相关性维度低于 hard gate："
                        f"{dimension_scores['paper_relevance']:.3f} < {relevance_threshold:.3f}。"
                    ),
                    recommended_action="补充检索并重新排序论文。",
                )
            )
        evidence_threshold = self.policy.hard_gate_min_scores["evidence_support"]
        if (
            dimension_scores["evidence_grounding"] < evidence_threshold
            and "evidence_grounding" not in existing_hard_dimensions
        ):
            issues.append(
                QualityIssue(
                    issue_type="unsupported_claim",
                    severity="error",
                    target_path="evidence_claims",
                    message=(
                        "证据支持维度低于 hard gate："
                        f"{dimension_scores['evidence_grounding']:.3f} < {evidence_threshold:.3f}。"
                    ),
                    recommended_action="补充带摘要的论文并重写证据论述。",
                )
            )

    def _hard_gate_results(
        self,
        dimension_scores: dict[str, float],
        issues: list[QualityIssue],
    ) -> list[QualityGateResult]:
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
            gate_score = min(
                (dimension_scores.get(dimension, 0.0) for dimension in dimensions),
                default=0.0,
            )
            gate_threshold = self.policy.hard_gate_min_scores[gate]
            results.append(
                QualityGateResult(
                    gate=gate,
                    status=(
                        "failed"
                        if issue_ids or gate_score < gate_threshold
                        else "passed"
                    ),
                    issue_ids=issue_ids,
                    score=round(gate_score, 6),
                    threshold=gate_threshold,
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
        stage_ids = {str(item.stage_id) for item in output.development_stages}
        problem_ids = {str(item.problem_id) for item in output.current_landscape.problem_details}
        subdirection_ids = {str(item.subdirection_id) for item in output.current_landscape.subdirection_details}
        problem_by_id = {
            str(item.problem_id): item
            for item in output.current_landscape.problem_details
        }
        direction_by_id = {
            str(item.subdirection_id): item
            for item in output.current_landscape.subdirection_details
        }
        stage_relation_score = _average(
            float(
                bool(stage.breakthroughs)
                and (not problem_ids or bool(stage.related_problem_ids))
                and set(stage.related_problem_ids) <= problem_ids
                and all(
                    bool(item.description.strip())
                    and bool(item.supporting_paper_ids)
                    and set(item.supporting_paper_ids) <= set(stage.related_paper_ids)
                    and (not problem_ids or bool(item.limitation_problem_ids))
                    and set(item.limitation_problem_ids) <= problem_ids
                    for item in stage.breakthroughs
                )
            )
            for stage in output.development_stages
        )
        relation_contract_present = any(
            stage.breakthroughs or stage.related_problem_ids
            for stage in output.development_stages
        )
        problem_detail_score = _average(
            float(
                bool(item.description.strip())
                and bool(item.related_paper_ids)
                and bool(item.related_stage_ids)
                and bool(item.emerged_in_stage_id)
                and bool(item.affected_stage_ids)
                and bool(item.related_subdirection_ids)
                and item.emerged_in_stage_id in stage_ids
                and set(item.affected_stage_ids) <= stage_ids
                and set(item.related_subdirection_ids) <= subdirection_ids
                and (
                    not relation_contract_present
                    or all(
                        str(item.problem_id)
                        in direction_by_id[direction_id].addresses_problem_ids
                        for direction_id in item.related_subdirection_ids
                    )
                )
            )
            for item in output.current_landscape.problem_details
        )
        subdirection_detail_score = _average(
            float(
                bool(item.description.strip())
                and bool(item.why_it_matters.strip())
                and bool(item.research_questions)
                and bool(item.related_paper_ids)
                and bool(item.related_stage_ids)
                and bool(item.emerged_in_stage_id)
                and bool(item.addresses_problem_ids)
                and item.emerged_in_stage_id in stage_ids
                and set(item.addresses_problem_ids) <= problem_ids
                and (
                    not relation_contract_present
                    or all(
                        str(item.subdirection_id)
                        in problem_by_id[problem_id].related_subdirection_ids
                        for problem_id in item.addresses_problem_ids
                    )
                )
            )
            for item in output.current_landscape.subdirection_details
        )
        values = [
            _coverage(len(output.prerequisites), 3),
            _coverage(len(output.development_stages), self.config.min_development_stages),
            _coverage(len(output.current_landscape.problems), 3),
            _coverage(len(output.current_landscape.subdirections), self.config.min_subdirections),
            problem_detail_score,
            subdirection_detail_score,
        ]
        score = _average(values)
        if relation_contract_present and stage_relation_score < 1.0:
            issues.append(
                QualityIssue(
                    issue_type="missing_coverage",
                    severity="warning",
                    target_path="development_stages",
                    message="发展阶段、关键突破与遗留问题之间的关系证据不完整。",
                    recommended_action="补齐突破的论文依据和可验证的问题 ID，不要按位置推断关系。",
                )
            )
        if score < 1.0:
            issues.append(
                QualityIssue(
                    issue_type="missing_coverage",
                    severity="warning",
                    target_path="current_landscape",
                    message="领域分支、问题、前置知识、发展阶段或全景证据覆盖不足。",
                    recommended_action="围绕缺失问题和子方向补充论文、阶段关联与可验证描述。",
                )
            )
        return score

    def evaluate_paper_relevance(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
        issues: list[QualityIssue],
    ) -> float:
        allowed = {paper.paper_id: paper for paper in allowed_papers}
        selected = [allowed[paper.paper_id] for paper in output.papers if paper.paper_id in allowed]
        if not selected:
            score = 0.0
            low_ids: list[str] = []
        else:
            threshold = self.config.quality_min_paper_relevance_score
            context_mismatch_ids = [
                paper.paper_id for paper in selected if paper.context_score <= 0.0
            ]
            low_ids = [
                paper.paper_id for paper in selected if paper.relevance_score < threshold
            ]
            relevant_ratio = 1.0 - len(low_ids) / len(selected)
            mean_relevance = _average(paper.relevance_score for paper in selected)
            minimum_relevance = min(paper.relevance_score for paper in selected)
            calibration_scale = max(threshold * 4.0, 0.2)
            calibrated_mean = min(1.0, mean_relevance / calibration_scale)
            calibrated_minimum = min(1.0, minimum_relevance / calibration_scale)
            score = (
                0.5 * relevant_ratio
                + 0.3 * calibrated_mean
                + 0.2 * calibrated_minimum
            )
            if context_mismatch_ids:
                issues.append(
                    QualityIssue(
                        issue_type="paper_context_mismatch",
                        severity="error",
                        target_path="papers",
                        message=(
                            "推荐论文与目标领域存在同词异义语境冲突；论文 ID："
                            f"{context_mismatch_ids}。"
                        ),
                        recommended_action="重新检索并排除属于其他学科语境的同名论文。",
                    )
                )
            if self.config.enforce_core_paper_coverage:
                canonical_specs = self.canonical_registry.specs(output.domain)
                if canonical_specs:
                    canonical_count = sum(
                        self.canonical_registry.match(paper, output.domain) is not None
                        for paper in selected
                    )
                    target = min(self.config.min_core_papers, len(canonical_specs))
                    if target > 0:
                        core_coverage = min(1.0, canonical_count / target)
                        score *= 0.75 + 0.25 * core_coverage
                    if target > 0 and canonical_count < target:
                        issues.append(
                            QualityIssue(
                                issue_type="missing_core_paper",
                                severity="error",
                                target_path="papers",
                                message=(
                                    f"核心论文覆盖不足：{canonical_count}/{target}；"
                                    f"核心论文表版本 {self.canonical_registry.version}。"
                                ),
                                recommended_action="补充检索该领域的奠基或核心方法论文。",
                            )
                        )
        if score < self.config.quality_paper_relevance_threshold:
            severity = "error" if not selected or len(low_ids) * 2 >= len(selected) else "warning"
            issues.append(
                QualityIssue(
                    issue_type="low_paper_relevance",
                    severity=severity,
                    target_path="papers",
                    message=f"推荐论文主题相关性不足；低相关论文 ID：{low_ids}。",
                    recommended_action="补充检索并重新排序，只保留与规划主题直接相关的论文。",
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
            reference_ids = {paper.paper_id for paper in stage.representative_papers}
            guidance_complete = all(
                paper.contribution.strip() and paper.reading_focus
                for paper in stage.representative_papers
            )
            checks = [
                stage.sequence == index + 1,
                bool(stage.period.strip()),
                not bool(re.search(r"\bweek\b|周", stage.historical_period, re.IGNORECASE)),
                stage.start_year is None or stage.end_year is None or stage.start_year <= stage.end_year,
                (
                    index == 0
                    or stage.start_year is None
                    or output.development_stages[index - 1].end_year is None
                    or stage.start_year
                    >= output.development_stages[index - 1].end_year
                ),
                bool(stage.summary.strip()),
                bool(stage.motivation.strip()),
                (
                    stage.previous_stage_id is None
                    if index == 0
                    else stage.previous_stage_id
                    == output.development_stages[index - 1].stage_id
                ),
                index == 0 or bool(stage.transition_from_previous.strip()),
                bool(stage.related_paper_ids),
                bool(stage.core_concepts),
                bool(stage.main_techniques),
                bool(stage.open_problems),
                reference_ids == set(stage.related_paper_ids),
                guidance_complete,
                bool(stage.prerequisite_ids),
            ]
            stage_scores.append(sum(checks) / len(checks))
            if not all(checks):
                issues.append(
                    QualityIssue(
                        issue_type="weak_development_stage",
                        severity="warning",
                        target_path=f"development_stages[{index}]",
                        message="发展阶段顺序、时期、前后承接、论文或内容字段不完整。",
                        recommended_action="按时间顺序补充该阶段的时期、前驱阶段和技术转折说明。",
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
                        all(
                            paper.contribution.strip() and paper.reading_focus
                            for paper in step.papers
                        ),
                        bool(step.deliverables),
                        (
                            bool(step.reproducibility_checklist and step.evaluation_metrics)
                            if step.step == "4"
                            else True
                        ),
                        not self._learning_step_has_embedded_structure(step),
                    ]
                )
                / 9
                for step in output.learning_path
            )
        )
        malformed_steps = [
            index
            for index, step in enumerate(output.learning_path)
            if self._learning_step_has_embedded_structure(step)
        ]
        if malformed_steps:
            issues.append(
                QualityIssue(
                    issue_type="format_error",
                    severity="error",
                    target_path="learning_path",
                    message=f"学习路径包含被字符串化的结构化内容；步骤索引：{malformed_steps}。",
                    recommended_action="丢弃非字符串列表项，并重新生成对应学习步骤。",
                )
            )
        role_by_id = {paper.paper_id: paper.paper_role for paper in output.papers}
        priority_by_id = {
            paper.paper_id: paper.reading_priority for paper in output.papers
        }
        early_ids = {
            paper_id
            for step in output.learning_path[:3]
            for paper_id in step.paper_ids
        }
        first_two_ids = {
            paper_id
            for step in output.learning_path[:2]
            for paper_id in step.paper_ids
        }
        core_ids = {
            paper_id
            for paper_id, priority in priority_by_id.items()
            if priority == "core"
        }
        late_role_ids = {
            paper_id
            for paper_id, role in role_by_id.items()
            if role in {"application", "frontier"}
        }
        route_fit = (not core_ids or bool(core_ids & early_ids)) and not (
            late_role_ids & first_two_ids
        )
        route_fit = route_fit and not any(
            set(previous.paper_ids) & set(current.paper_ids)
            for previous, current in zip(
                output.learning_path, output.learning_path[1:]
            )
        )
        if len(output.learning_path) >= 4:
            method_roles = {
                role_by_id.get(paper_id)
                for paper_id in output.learning_path[2].paper_ids
            }
            experiment_roles = {
                role_by_id.get(paper_id)
                for paper_id in output.learning_path[3].paper_ids
            }
            route_fit = route_fit and bool(
                method_roles & {"foundational", "method"}
            ) and bool(experiment_roles & {"method", "evaluation", "application"})
        time_fit = self._learning_time_fit(output)
        score = (
            0.15 * float(sequence_ok)
            + 0.60 * item_score
            + 0.10 * float(route_fit)
            + 0.15 * float(time_fit)
        )
        if score < 1.0:
            issues.append(
                QualityIssue(
                    issue_type="route_conflict",
                    severity="error" if not sequence_ok else "warning",
                    target_path="learning_path",
                    message="学习步骤不连续、缺少阅读指导、论文位置不合理，或未覆盖用户时间预算。",
                    recommended_action="按基础到实验再到前沿的固定阶段重新编号，并补齐周次、里程碑和验收条件。",
                )
            )
        return score

    @staticmethod
    def _learning_step_has_embedded_structure(step: object) -> bool:
        values = [
            *getattr(step, "topics", []),
            *getattr(step, "activities", []),
            *getattr(step, "completion_criteria", []),
            *getattr(step, "deliverables", []),
            *getattr(step, "reproducibility_checklist", []),
            *getattr(step, "evaluation_metrics", []),
        ]
        return any(
            (text := str(value).strip()).startswith(("{", "["))
            and text.endswith(("}", "]"))
            for value in values
        )

    def evaluate_language_alignment(
        self,
        output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> float:
        prose = " ".join(
            [
                output.text,
                *(item.why_needed for item in output.prerequisites),
                *(item.summary for item in output.development_stages),
                *(item.description for item in output.current_landscape.problem_details),
                *(item.description for item in output.current_landscape.subdirection_details),
                *(item.goal for item in output.learning_path),
            ]
        )
        cjk = len(re.findall(r"[\u4e00-\u9fff]", prose))
        latin_words = len(re.findall(r"\b[A-Za-z]{3,}\b", prose))
        if output.language == "zh-CN":
            score = min(1.0, cjk / max(80, latin_words * 2))
        else:
            score = min(1.0, latin_words / max(40, cjk))
        if score < 0.75:
            issues.append(
                QualityIssue(
                    issue_type="language_mismatch",
                    severity="warning",
                    target_path="$",
                    message="说明性内容与请求语言不一致。",
                    recommended_action="保留论文标题和专业术语，其余说明改写为请求语言。",
                )
            )
        return score

    @staticmethod
    def _learning_time_fit(output: DomainOnboardingOutput) -> bool:
        total_weeks = output.learner_profile.time_budget_weeks
        if total_weeks is None:
            return True
        if not output.learning_path:
            return False
        ranges = [(step.start_week, step.end_week) for step in output.learning_path]
        if any(start is None or end is None for start, end in ranges):
            return False
        normalized = [(int(start), int(end)) for start, end in ranges]
        return (
            normalized[0][0] == 1
            and normalized[-1][1] == total_weeks
            and all(1 <= start <= end <= total_weeks for start, end in normalized)
            and all(
                normalized[index][0] >= normalized[index - 1][0]
                and normalized[index][1] >= normalized[index - 1][1]
                for index in range(1, len(normalized))
            )
            and all(step.milestone.strip() for step in output.learning_path)
        )

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
        for problem in output.current_landscape.problem_details:
            yield from problem.related_paper_ids
        for subdirection in output.current_landscape.subdirection_details:
            yield from subdirection.related_paper_ids


def critical_dimensions_not_regressed(
    first: ContentQuality,
    retry: ContentQuality,
) -> bool:
    for name in (
        "structure", "paper_validity", "paper_relevance", "evidence_grounding", "learning_path"
    ):
        if retry.dimensions.get(name, 0.0) + 1e-9 < first.dimensions.get(name, 0.0):
            return False
    return True
