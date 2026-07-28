"""Shadow evaluation for graph-constrained onboarding paths."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from handlers.domain_onboarding.graph_path_planner import GraphBasedPathPlanner
from handlers.domain_onboarding.graph_validator import DomainKnowledgeGraphValidator
from handlers.domain_onboarding.schemas import (
    GraphValidationReport,
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    KnowledgeGraphSnapshot,
)


class GraphEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphPathNode(GraphEvaluationModel):
    node_id: str = Field(min_length=1)
    node_type: Literal["prerequisite", "development_stage"]
    label: str = Field(min_length=1)


class GraphPathDependency(GraphEvaluationModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    edge_type: Literal["precedes", "requires"]


class GraphPathEvaluationCase(GraphEvaluationModel):
    case_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    nodes: list[GraphPathNode] = Field(min_length=1)
    dependencies: list[GraphPathDependency] = Field(default_factory=list)
    baseline_order: list[str] = Field(min_length=1)
    expected_fallback: bool
    annotation_status: Literal["seed", "human_verified"] = "seed"

    @model_validator(mode="after")
    def validate_baseline(self) -> "GraphPathEvaluationCase":
        node_ids = {node.node_id for node in self.nodes}
        if set(self.baseline_order) != node_ids:
            raise ValueError("baseline_order must contain every node exactly once")
        return self


class GraphPathEvaluationReport(GraphEvaluationModel):
    dataset_version: str
    case_count: int
    annotation_status_counts: dict[str, int]
    graph_validity_rate: float
    fallback_rate: float
    expected_behavior_accuracy: float
    dependency_order_rate: float
    node_coverage_rate: float
    stage_order_preservation_rate: float
    promotion_recommended: bool
    failed_case_ids: list[str]


def load_graph_path_cases(path: str | Path) -> list[GraphPathEvaluationCase]:
    source = Path(path)
    cases: list[GraphPathEvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = GraphPathEvaluationCase.model_validate(json.loads(line))
        except Exception as error:
            raise ValueError(f"invalid graph path case at {source}:{line_number}: {error}") from error
        if case.case_id in seen:
            raise ValueError(f"duplicate graph path case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"graph path dataset is empty: {source}")
    return cases


def evaluate_graph_paths(
    cases: list[GraphPathEvaluationCase],
    *,
    dataset_version: str = "domain-graph-path-shadow-v1",
) -> GraphPathEvaluationReport:
    if not cases:
        raise ValueError("at least one graph path case is required")
    valid_count = fallback_count = behavior_correct = 0
    dependency_checks = dependency_passes = 0
    coverage_values: list[float] = []
    stage_checks = stage_passes = 0
    failed: list[str] = []
    validator = DomainKnowledgeGraphValidator()
    planner = GraphBasedPathPlanner()
    for case in cases:
        graph = _to_graph(case)
        graph.validation = validator.validate(graph)
        plan = planner.plan(graph)
        valid_count += graph.validation.valid
        fallback_count += plan.fallback_used
        behavior_ok = plan.fallback_used == case.expected_fallback
        behavior_correct += behavior_ok
        if plan.fallback_used:
            coverage_values.append(0.0)
        else:
            positions = {node_id: index for index, node_id in enumerate(plan.ordered_node_ids)}
            coverage_values.append(len(positions) / len(case.nodes))
            for dependency in case.dependencies:
                dependency_checks += 1
                source, target = (
                    (dependency.target_id, dependency.source_id)
                    if dependency.edge_type == "requires"
                    else (dependency.source_id, dependency.target_id)
                )
                dependency_passes += positions[source] < positions[target]
            baseline_stages = [
                node_id
                for node_id in case.baseline_order
                if next(node for node in case.nodes if node.node_id == node_id).node_type
                == "development_stage"
            ]
            planned_stages = [node_id for node_id in plan.ordered_node_ids if node_id in baseline_stages]
            stage_checks += 1
            stage_passes += planned_stages == baseline_stages
        if not behavior_ok:
            failed.append(case.case_id)
    validity_rate = _ratio(valid_count, len(cases))
    fallback_rate = _ratio(fallback_count, len(cases))
    dependency_rate = _ratio(dependency_passes, dependency_checks)
    coverage_rate = round(sum(coverage_values) / len(coverage_values), 6)
    stage_rate = _ratio(stage_passes, stage_checks)
    return GraphPathEvaluationReport(
        dataset_version=dataset_version,
        case_count=len(cases),
        annotation_status_counts=dict(sorted(Counter(case.annotation_status for case in cases).items())),
        graph_validity_rate=validity_rate,
        fallback_rate=fallback_rate,
        expected_behavior_accuracy=_ratio(behavior_correct, len(cases)),
        dependency_order_rate=dependency_rate,
        node_coverage_rate=coverage_rate,
        stage_order_preservation_rate=stage_rate,
        promotion_recommended=(
            validity_rate >= 0.95
            and fallback_rate <= 0.05
            and dependency_rate == 1.0
            and coverage_rate >= 0.95
            and stage_rate == 1.0
        ),
        failed_case_ids=failed,
    )


def _to_graph(case: GraphPathEvaluationCase) -> KnowledgeGraphSnapshot:
    return KnowledgeGraphSnapshot(
        request_id=case.case_id,
        quality_policy_version="domain-quality-v1.1.0",
        nodes=[
            KnowledgeGraphNode(
                node_id=node.node_id,
                node_type=node.node_type,
                label=node.label,
                source_path=f"fixture.{node.node_id}",
            )
            for node in case.nodes
        ],
        edges=[
            KnowledgeGraphEdge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                edge_type=edge.edge_type,
                source_path="fixture.dependencies",
            )
            for edge in case.dependencies
        ],
        validation=GraphValidationReport(valid=False),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
