"""排序离线基准的可复用指标计算。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import RankedPaper


@dataclass(frozen=True, slots=True)
class RankingBenchmarkMetrics:
    precision_at_k: float
    recall_at_k: float
    ndcg_at_k: float
    role_coverage: int


def evaluate_ranking(
    papers: list[RankedPaper],
    relevance_grades: dict[str, int],
    *,
    k: int,
) -> RankingBenchmarkMetrics:
    if k < 1:
        raise ValueError("k must be positive")
    selected = papers[:k]
    relevant = {paper_id for paper_id, grade in relevance_grades.items() if grade > 0}
    selected_relevant = sum(paper.paper_id in relevant for paper in selected)
    precision = selected_relevant / k
    recall = selected_relevant / len(relevant) if relevant else 1.0
    gains = [relevance_grades.get(paper.paper_id, 0) for paper in selected]
    ideal = sorted(relevance_grades.values(), reverse=True)[:k]
    dcg = _discounted_gain(gains)
    ideal_dcg = _discounted_gain(ideal)
    return RankingBenchmarkMetrics(
        precision_at_k=round(precision, 6),
        recall_at_k=round(recall, 6),
        ndcg_at_k=round(dcg / ideal_dcg if ideal_dcg else 1.0, 6),
        role_coverage=len({paper.paper_role for paper in selected if paper.paper_role != "other"}),
    )


def _discounted_gain(grades: list[int]) -> float:
    return sum(
        (2**grade - 1) / math.log2(index + 2)
        for index, grade in enumerate(grades)
    )
