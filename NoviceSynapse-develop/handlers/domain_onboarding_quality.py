"""评估领域入门结果的内容完整度并构造受限重试提示。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from handlers.domain_onboarding_schema import (
    ContentQuality,
    DomainOnboardingOutput,
)

HIGH_QUALITY_THRESHOLD = 90


@dataclass(frozen=True, slots=True)
class QualityScoringPolicy:
    domain_summary_weight: int = 10
    prerequisites_weight: int = 20
    development_stages_weight: int = 30
    current_landscape_weight: int = 15
    learning_path_weight: int = 25
    retry_threshold: int = 75

    def weights(self) -> dict[str, int]:
        return {
            key.removesuffix("_weight"): value
            for key, value in asdict(self).items()
            if key.endswith("_weight")
        }

    def validate(self) -> None:
        if sum(self.weights().values()) != 100:
            raise ValueError("Quality scoring weights must sum to 100.")
        if not 0 <= self.retry_threshold <= 100:
            raise ValueError("retry_threshold must be between 0 and 100.")


@dataclass(frozen=True, slots=True)
class QualityFeatureVector:
    domain_summary: float
    prerequisites: float
    development_stages: float
    current_landscape: float
    learning_path: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def score(self, policy: QualityScoringPolicy) -> int:
        policy.validate()
        values = self.as_dict()
        score = sum(
            values[dimension] * weight
            for dimension, weight in policy.weights().items()
        )
        return max(0, min(100, round(score)))


BASELINE_POLICY = QualityScoringPolicy()
CALIBRATED_POLICY = QualityScoringPolicy(
    domain_summary_weight=15,
    prerequisites_weight=20,
    development_stages_weight=25,
    current_landscape_weight=15,
    learning_path_weight=25,
    retry_threshold=89,
)
QUALITY_THRESHOLD = CALIBRATED_POLICY.retry_threshold


def _coverage(actual: int, target: int) -> float:
    return min(actual / target, 1.0)


def _has_text(value: str) -> bool:
    return bool(value.strip())


def _average_item_coverage(
    items: Sequence[Any],
    checks: Sequence[Callable[[Any], bool]],
) -> float:
    if not items:
        return 0.0

    completed = sum(1 for item in items for check in checks if check(item))
    return completed / (len(items) * len(checks))


def _quality_level(score: int, threshold: int) -> str:
    if score >= HIGH_QUALITY_THRESHOLD:
        return "high"
    if score >= threshold:
        return "acceptable"
    return "low"


def extract_quality_features(
    output: DomainOnboardingOutput,
) -> tuple[QualityFeatureVector, list[str]]:
    issues: list[str] = []
    domain_summary = (
        (3.0 if _has_text(output.domain) else 0.0)
        + 7.0 * _coverage(len(output.text.strip()), 40)
    ) / 10.0
    if len(output.text.strip()) < 40:
        issues.append("text 摘要不足 40 个字符")

    prerequisite_coverage = _average_item_coverage(
        output.prerequisites,
        (
            lambda item: _has_text(item.why_needed),
            lambda item: bool(item.key_points),
        ),
    )
    prerequisites = (
        8.0 * _coverage(len(output.prerequisites), 3)
        + 12.0 * prerequisite_coverage
    ) / 20.0
    if len(output.prerequisites) < 3:
        issues.append("prerequisites 少于 3 项")
    if output.prerequisites and prerequisite_coverage < 1.0:
        issues.append("部分 prerequisites 缺少 why_needed 或 key_points")

    stage_coverage = _average_item_coverage(
        output.development_stages,
        (
            lambda item: _has_text(item.summary),
            lambda item: _has_text(item.motivation),
            lambda item: bool(item.representative_papers),
            lambda item: bool(item.core_concepts),
            lambda item: bool(item.main_techniques),
            lambda item: bool(item.open_problems),
        ),
    )
    development_stages = (
        10.0 * _coverage(len(output.development_stages), 3)
        + 20.0 * stage_coverage
    ) / 30.0
    if len(output.development_stages) < 3:
        issues.append("development_stages 少于 3 个阶段")
    if output.development_stages and stage_coverage < 1.0:
        issues.append("部分 development_stages 缺少阶段说明、论文、概念、技术或问题")

    current_landscape = (
        _coverage(len(output.current_landscape.problems), 3)
        + _coverage(len(output.current_landscape.subdirections), 3)
    ) / 2.0
    if len(output.current_landscape.problems) < 3:
        issues.append("current_landscape.problems 少于 3 项")
    if len(output.current_landscape.subdirections) < 3:
        issues.append("current_landscape.subdirections 少于 3 项")

    learning_coverage = _average_item_coverage(
        output.learning_path,
        (
            lambda item: _has_text(item.goal),
            lambda item: bool(item.topics),
            lambda item: bool(item.papers),
            lambda item: _has_text(item.expected_outcome),
        ),
    )
    learning_path = (
        8.0 * _coverage(len(output.learning_path), 3)
        + 17.0 * learning_coverage
    ) / 25.0
    if len(output.learning_path) < 3:
        issues.append("learning_path 少于 3 个步骤")
    if output.learning_path and learning_coverage < 1.0:
        issues.append("部分 learning_path 缺少目标、主题、论文或预期成果")

    return (
        QualityFeatureVector(
            domain_summary=domain_summary,
            prerequisites=prerequisites,
            development_stages=development_stages,
            current_landscape=current_landscape,
            learning_path=learning_path,
        ),
        issues,
    )


def evaluate_content_quality(
    output: DomainOnboardingOutput,
    policy: QualityScoringPolicy = CALIBRATED_POLICY,
) -> ContentQuality:
    features, issues = extract_quality_features(output)
    final_score = features.score(policy)
    return ContentQuality(
        score=final_score,
        threshold=policy.retry_threshold,
        level=_quality_level(final_score, policy.retry_threshold),
        issues=issues,
    )


def build_quality_retry_prompt(
    query: str,
    quality: ContentQuality,
) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in quality.issues)
    return (
        f"用户原始研究方向：{query}\n"
        f"上一次输出结构合法，但内容完整度为 {quality.score}/100，"
        f"低于最低要求 {quality.threshold}/100。\n"
        "请重新生成一份更完整的领域入门方案，重点补齐以下内容：\n"
        f"{issue_lines}\n"
        "这是唯一一次修正机会。必须继续严格遵守系统消息中的 JSON Schema，"
        "只输出一个 JSON object，不要解释评分或重试过程。"
    )
