"""将修复候选的质量变化转换为可解释的结果选择决策。"""

from __future__ import annotations

from .schemas import ContentQuality, RepairDecision, RepairRecord


class RepairSelectionPolicy:
    default_critical_dimensions = (
        "structure",
        "paper_validity",
        "paper_relevance",
        "evidence_grounding",
        "learning_path",
    )

    def __init__(
        self,
        min_improvement_delta: float,
        critical_dimensions: list[str] | tuple[str, ...] | None = None,
    ):
        self.min_improvement_delta = min_improvement_delta
        self.critical_dimensions = tuple(
            critical_dimensions or self.default_critical_dimensions
        )

    def initial(self, quality: ContentQuality) -> RepairDecision:
        return RepairDecision(
            selected_attempt=1,
            decision="initial_selected",
            reasons=["quality_threshold_met"],
            score_delta=0.0,
            dimension_deltas={name: 0.0 for name in quality.dimensions},
        )

    def decide(
        self,
        first: ContentQuality,
        retry: ContentQuality,
        repair_record: RepairRecord,
    ) -> RepairDecision:
        score_delta = round(retry.score - first.score, 6)
        dimension_deltas = {
            name: round(
                retry.dimensions.get(name, 0.0) - first.dimensions.get(name, 0.0),
                6,
            )
            for name in sorted(set(first.dimensions) | set(retry.dimensions))
        }
        reasons = []
        if not retry.passed_hard_gates:
            reasons.append("hard_gate_failed")
        if score_delta + 1e-9 < self.min_improvement_delta:
            reasons.append("improvement_too_small")
        if any(dimension_deltas.get(name, 0.0) < -1e-9 for name in self.critical_dimensions):
            reasons.append("critical_dimension_regressed")

        if not reasons:
            return RepairDecision(
                selected_attempt=2,
                decision="repaired_selected",
                reasons=["significant_improvement"],
                score_delta=score_delta,
                dimension_deltas=dimension_deltas,
            )

        if any(action.status == "failed" for action in repair_record.actions):
            reasons.append("repair_execution_failed")
        return RepairDecision(
            selected_attempt=1,
            decision="initial_retained",
            reasons=list(dict.fromkeys(reasons)),
            score_delta=score_delta,
            dimension_deltas=dimension_deltas,
        )
