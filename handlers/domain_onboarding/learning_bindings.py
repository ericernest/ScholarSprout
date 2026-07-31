"""Bind papers to learning tasks with explicit, explainable purposes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import (
    CurrentLandscape,
    DevelopmentStage,
    LearningPaperBinding,
    LearningStep,
    LearningUse,
    PaperReference,
    PaperRole,
    RankedPaper,
    ReadingMode,
)


@dataclass(frozen=True)
class StepBindingPolicy:
    learning_use: LearningUse
    reading_mode: ReadingMode
    preferred_roles: tuple[PaperRole, ...]
    capability: str


class LearningPaperBinder:
    """Select papers for what the learner must do, not merely by paper role."""

    _POLICIES = {
        1: StepBindingPolicy(
            "concept_introduction", "read", ("survey", "foundational"), "concept"
        ),
        2: StepBindingPolicy(
            "architecture_reference",
            "read",
            ("foundational", "method", "survey"),
            "architecture",
        ),
        3: StepBindingPolicy(
            "method_extension", "read", ("method", "foundational"), "method"
        ),
        4: StepBindingPolicy(
            "baseline_implementation",
            "reproduce",
            ("method", "foundational"),
            "implementation",
        ),
        5: StepBindingPolicy(
            "frontier_problem", "read", ("frontier", "method"), "frontier"
        ),
    }

    _CAPABILITY_PATTERNS = {
        "concept": (
            r"\bsurvey\b",
            r"\boverview\b",
            r"\bfoundation(?:al)?\b",
            r"retrieval.augmented generation for knowledge.intensive",
        ),
        "architecture": (
            r"\barchitecture\b",
            r"\bframework\b",
            r"\brecipe\b",
            r"dense passage retrieval",
            r"retrieval.augmented generation for knowledge.intensive",
        ),
        "method": (
            r"\bmethod\b",
            r"\bapproach\b",
            r"\bmodel\b",
            r"\blearning to\b",
            r"\benhanc(?:e|ing)\b",
        ),
        "implementation": (
            r"\bimplementation\b",
            r"\bbaseline\b",
            r"\bsystem\b",
            r"\bpipeline\b",
            r"\bframework\b",
            r"dense passage retrieval",
            r"retrieval.augmented generation for knowledge.intensive",
        ),
        "benchmark": (r"\bbenchmark", r"\bdataset\b", r"\bcorpus\b"),
        "evaluation": (
            r"\bevaluat",
            r"\bmetric",
            r"\bragas\b",
            r"\bfaithfulness\b",
        ),
        "frontier": (
            r"\bself.rag\b",
            r"\bcorrective\b",
            r"\badaptive\b",
            r"\bagentic\b",
            r"\breflection\b",
            r"\bcritique\b",
            r"\breasoning\b",
        ),
    }

    def bind(
        self,
        steps: list[LearningStep],
        papers: list[RankedPaper],
        stages: list[DevelopmentStage],
        landscape: CurrentLandscape,
        references: dict[str, PaperReference],
        *,
        language: str,
    ) -> list[LearningStep]:
        if not papers:
            return steps
        paper_by_id = {paper.paper_id: paper for paper in papers}
        used_ids: set[str] = set()
        for position, step in enumerate(steps, start=1):
            policy = self._POLICIES.get(position, self._POLICIES[5])
            suggested = {
                paper_id for paper_id in step.paper_ids if paper_id in paper_by_id
            }
            primary, primary_signals, matched = self._select_primary(
                papers, policy, suggested, used_ids
            )
            selected = [(primary, policy.learning_use, policy.reading_mode, True, primary_signals, matched)]
            if position == 4:
                companion = self._select_evaluation_companion(
                    papers, used_ids | {primary.paper_id}
                )
                if companion is not None:
                    companion_paper, companion_use, signals = companion
                    selected.append(
                        (
                            companion_paper,
                            companion_use,
                            "evaluate",
                            False,
                            signals,
                            True,
                        )
                    )
            step.paper_ids = [paper.paper_id for paper, *_ in selected]
            step.papers = [
                references[paper_id].model_copy(deep=True)
                for paper_id in step.paper_ids
                if paper_id in references
            ]
            step.paper_bindings = [
                LearningPaperBinding(
                    paper_id=paper.paper_id,
                    learning_use=learning_use,
                    reason=self._reason(
                        paper, learning_use, signals, language=language
                    ),
                    reading_mode=reading_mode,
                    required=required,
                    binding_status="policy_matched" if policy_matched else "fallback",
                    matched_signals=signals,
                )
                for paper, learning_use, reading_mode, required, signals, policy_matched in selected
            ]
            used_ids.update(step.paper_ids)
            self._attach_relation_ids(step, stages, landscape)
        return steps

    def _select_primary(
        self,
        papers: list[RankedPaper],
        policy: StepBindingPolicy,
        suggested: set[str],
        used_ids: set[str],
    ) -> tuple[RankedPaper, list[str], bool]:
        role_candidates = [
            paper for paper in papers if paper.paper_role in policy.preferred_roles
        ]
        if policy.learning_use in {"concept_introduction", "architecture_reference"}:
            role_candidates = [
                paper
                for paper in role_candidates
                if paper.paper_role not in {"application", "frontier"}
            ]
        if policy.learning_use == "frontier_problem":
            role_candidates = [
                paper for paper in role_candidates if paper.paper_role != "application"
            ]
        capability_candidates = [
            paper
            for paper in role_candidates
            if self._signals(paper, policy.capability)
        ]
        unused_capability = [
            paper
            for paper in capability_candidates
            if paper.paper_id not in used_ids
        ]
        unused_role = [
            paper for paper in role_candidates if paper.paper_id not in used_ids
        ]
        candidates = (
            unused_capability
            or capability_candidates
            or unused_role
            or role_candidates
            or [paper for paper in papers if paper.paper_role != "application"]
            or papers
        )
        ranked = sorted(
            candidates,
            key=lambda paper: self._selection_key(
                paper, policy, suggested, used_ids
            ),
            reverse=True,
        )
        chosen = ranked[0]
        signals = self._signals(chosen, policy.capability)
        matched = (
            chosen.paper_role in policy.preferred_roles and bool(signals)
        )
        signals = list(
            dict.fromkeys([*signals, f"paper_role:{chosen.paper_role}"])
        )
        return chosen, signals, matched

    def _select_evaluation_companion(
        self,
        papers: list[RankedPaper],
        excluded: set[str],
    ) -> tuple[RankedPaper, LearningUse, list[str]] | None:
        candidates = []
        for paper in papers:
            if paper.paper_id in excluded:
                continue
            benchmark = self._signals(paper, "benchmark")
            evaluation = self._signals(paper, "evaluation")
            if paper.paper_role == "evaluation" or benchmark or evaluation:
                learning_use: LearningUse = (
                    "benchmark_dataset" if benchmark else "evaluation_framework"
                )
                signals = list(dict.fromkeys([*benchmark, *evaluation]))
                signals.append(f"paper_role:{paper.paper_role}")
                candidates.append((paper, learning_use, signals))
        return max(
            candidates,
            key=lambda item: (
                item[0].is_canonical,
                item[0].paper_role == "evaluation",
                item[0].final_score,
            ),
            default=None,
        )

    def _selection_key(
        self,
        paper: RankedPaper,
        policy: StepBindingPolicy,
        suggested: set[str],
        used_ids: set[str],
    ) -> tuple[float, ...]:
        role_rank = (
            len(policy.preferred_roles) - policy.preferred_roles.index(paper.paper_role)
            if paper.paper_role in policy.preferred_roles
            else 0
        )
        signals = self._signals(paper, policy.capability)
        return (
            float(bool(signals)),
            float(role_rank),
            float(paper.is_canonical),
            float(paper.paper_id in suggested),
            float(paper.paper_id not in used_ids),
            paper.recency_score if policy.learning_use == "frontier_problem" else 0.0,
            paper.final_score,
        )

    def _signals(self, paper: RankedPaper, capability: str) -> list[str]:
        text = " ".join(
            [paper.title, paper.abstract or "", *paper.publication_types]
        ).lower()
        matched = any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in self._CAPABILITY_PATTERNS[capability]
        )
        return [f"capability:{capability}"] if matched else []

    @staticmethod
    def _reason(
        paper: RankedPaper,
        learning_use: LearningUse,
        signals: list[str],
        *,
        language: str,
    ) -> str:
        purpose_zh = {
            "concept_introduction": "建立基本概念",
            "architecture_reference": "理解标准架构",
            "method_extension": "掌握方法改进",
            "baseline_implementation": "复现可比较的基线",
            "benchmark_dataset": "选择基准与数据集",
            "evaluation_framework": "学习评价框架",
            "frontier_problem": "进入前沿问题",
        }[learning_use]
        purpose_en = learning_use.replace("_", " ")
        signal = signals[0].replace(":", " ") if signals else paper.paper_role
        if language == "en-US":
            return (
                f"Use this {paper.paper_role} paper for {purpose_en}; "
                f"the matched signal is {signal}."
            )
        return (
            f"将这篇{paper.paper_role}论文用于{purpose_zh}；"
            f"选择依据为 {signal} 信号。"
        )

    @staticmethod
    def _attach_relation_ids(
        step: LearningStep,
        stages: list[DevelopmentStage],
        landscape: CurrentLandscape,
    ) -> None:
        paper_ids = set(step.paper_ids)
        step.related_stage_ids = [
            str(stage.stage_id)
            for stage in stages
            if stage.stage_id and paper_ids & set(stage.related_paper_ids)
        ]
        step.related_problem_ids = [
            str(problem.problem_id)
            for problem in landscape.problem_details
            if problem.problem_id and paper_ids & set(problem.related_paper_ids)
        ]
        step.related_subdirection_ids = [
            str(direction.subdirection_id)
            for direction in landscape.subdirection_details
            if direction.subdirection_id
            and paper_ids & set(direction.related_paper_ids)
        ]
