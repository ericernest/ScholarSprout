"""仅基于已验证候选论文生成结构化领域入门内容。"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from .config import DomainOnboardingConfig
from .llm import StructuredLLMError, invoke_json
from .schemas import (
    CurrentLandscape,
    DevelopmentStage,
    DomainOnboardingOutput,
    DomainOnboardingRequest,
    DomainResearchPlan,
    EvidenceClaim,
    GenerationResult,
    LearnerProfile,
    LearningStep,
    ModelCallStats,
    PaperReference,
    Prerequisite,
    QualityIssue,
    RankedPaper,
    SelectedPaper,
)


class GenerationError(RuntimeError):
    def __init__(self, message: str, *, stats: ModelCallStats | None = None):
        super().__init__(message)
        self.stats = stats or ModelCallStats()


class OnboardingGenerator(Protocol):
    def generate(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
    ) -> GenerationResult: ...


class SimpleStagePathPlanner:
    stage_names = (
        "基础准备",
        "核心概念",
        "代表方法与论文",
        "工具、数据集与基线实验",
        "前沿问题与研究切入",
    )

    def normalize(
        self,
        raw_steps: object,
        *,
        profile: LearnerProfile,
        papers: list[RankedPaper],
        references: dict[str, PaperReference],
    ) -> list[LearningStep]:
        provided = raw_steps if isinstance(raw_steps, list) else []
        results: list[LearningStep] = []
        for index, stage_name in enumerate(self.stage_names, start=1):
            raw = provided[index - 1] if index - 1 < len(provided) and isinstance(provided[index - 1], dict) else {}
            ids = self._valid_ids(raw.get("paper_ids"), references)
            if not ids and papers:
                ids = [papers[min(index - 1, len(papers) - 1)].paper_id]
            activities = self._strings(raw.get("activities"))
            if profile.preference == "experiment_first" and index >= 3:
                activities.append("复现一个公开基线并记录实验配置与结果")
            elif profile.preference == "theory_first" and index <= 3:
                activities.append("整理概念定义、关键假设与方法推导笔记")
            elif not activities:
                activities.append("完成本阶段阅读清单并形成一页结构化笔记")
            criteria = self._strings(raw.get("completion_criteria")) or [
                "能够用自己的语言解释本阶段核心内容",
                "完成至少一项可检查的阅读或实验产出",
            ]
            goal = str(raw.get("goal") or f"完成{stage_name}并建立与下一阶段的连接")
            topics = self._strings(raw.get("topics")) or [stage_name]
            outcome = str(raw.get("expected_outcome") or criteria[0])
            results.append(
                LearningStep(
                    step=str(index),
                    goal=goal,
                    topics=topics,
                    paper_ids=ids,
                    papers=[references[paper_id] for paper_id in ids],
                    activities=list(dict.fromkeys(activities)),
                    completion_criteria=criteria,
                    expected_outcome=outcome,
                )
            )
        return results

    @staticmethod
    def _strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @staticmethod
    def _valid_ids(value: object, references: dict[str, PaperReference]) -> list[str]:
        values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        return list(dict.fromkeys(str(item) for item in values if str(item) in references))


class StructuredOnboardingGenerator:
    def __init__(self, model: Any, config: DomainOnboardingConfig):
        self.model = model
        self.config = config
        self.path_planner = SimpleStagePathPlanner()

    def generate(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
    ) -> GenerationResult:
        payload, stats = self._call_model(request, profile, plan, papers)
        try:
            output = self._normalize(payload, request, profile, plan, papers)
        except GenerationError as error:
            error.stats = stats
            raise
        return GenerationResult(output=output, stats=stats)

    def repair(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        previous_output: DomainOnboardingOutput,
        issues: list[QualityIssue],
    ) -> GenerationResult:
        payload, stats = self._call_model(
            request,
            profile,
            plan,
            papers,
            previous_output=previous_output,
            issues=issues,
        )
        try:
            output = self._normalize(payload, request, profile, plan, papers)
        except GenerationError as error:
            error.stats = stats
            raise
        return GenerationResult(output=output, stats=stats)

    def _call_model(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        *,
        previous_output: DomainOnboardingOutput | None = None,
        issues: list[QualityIssue] | None = None,
    ) -> tuple[dict[str, Any], ModelCallStats]:
        system_prompt = (
            "You generate a beginner-friendly Chinese domain onboarding plan. Return one JSON object only. "
            "You MUST use only paper_id values from allowed_papers; never invent or modify paper metadata. "
            "Output fields: domain, text, prerequisites, development_stages, current_landscape, learning_path. "
            "Each prerequisite has name, why_needed, key_points, related_paper_ids. Each development stage has "
            "name, summary, motivation, related_paper_ids, prerequisite_ids, core_concepts, main_techniques, open_problems. "
            "current_landscape has problems:list[str] and subdirections:list[str]; never put objects in either list. "
            "Each learning step has step, goal, topics, paper_ids, "
            "activities, completion_criteria, expected_outcome. Produce 3 development stages and 3-5 subdirections. "
            "Also output evidence_claims:[{claim,supporting_paper_ids,support_type}]. Important technical or historical "
            "claims must cite allowed paper IDs. support_type is abstract_explicit, metadata_inference, or background_synthesis. "
            "Use abstract_explicit only when every cited paper has a non-empty abstract and directly supports the claim; "
            "metadata_inference and background_synthesis are weak evidence and must not be phrased as proven facts. "
            "Use five ordered learning steps: 基础准备, 核心概念, 代表方法与论文, 工具、数据集与基线实验, 前沿问题与研究切入. "
            "Keep the JSON concise: exactly 3 prerequisites and 3 development stages, 3-5 subdirections, "
            "at most 3 items in each explanatory list, and at most 6 evidence claims. "
            "Return paper IDs only inside generated sections; paper metadata is attached by code."
        )
        user_payload: dict[str, Any] = {
            "request": request.model_dump(mode="json"),
            "learner_profile": profile.model_dump(mode="json"),
            "research_plan": plan.model_dump(mode="json"),
            "allowed_papers": [self._paper_prompt_payload(paper) for paper in papers],
        }
        if previous_output is not None:
            user_payload["previous_output"] = previous_output.model_dump(
                mode="json",
                exclude={"learner_profile", "papers"},
            )
            user_payload["repair_issues"] = [issue.model_dump(mode="json") for issue in issues or []]
            user_payload["instruction"] = "Repair only the reported weaknesses while preserving valid paper IDs."
        try:
            payload, stats = invoke_json(
                self.model,
                system_prompt=system_prompt,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                max_tokens=self.config.generation_max_tokens,
            )
            return payload, stats
        except StructuredLLMError as error:
            raise GenerationError(str(error), stats=error.stats) from error

    def _paper_prompt_payload(self, paper: RankedPaper) -> dict[str, Any]:
        abstract = (paper.abstract or "").strip()
        return {
            "paper_id": paper.paper_id,
            "title": paper.title,
            "abstract": abstract[: self.config.generation_paper_abstract_max_chars],
            "year": paper.year,
            "paper_role": paper.paper_role,
            "relevance_score": paper.relevance_score,
        }

    def _normalize(
        self,
        payload: dict[str, Any],
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
    ) -> DomainOnboardingOutput:
        references = {
            paper.paper_id: PaperReference(
                paper_id=paper.paper_id,
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                url=paper.url,
            )
            for paper in papers
        }
        prerequisites = self._normalize_prerequisites(payload.get("prerequisites"), references)
        stages = self._normalize_stages(payload.get("development_stages"), references, prerequisites, papers)
        landscape_raw = payload.get("current_landscape") if isinstance(payload.get("current_landscape"), dict) else {}
        problems = self._strings(landscape_raw.get("problems"))
        subdirections = self._strings(landscape_raw.get("subdirections")) or plan.expected_subdirections
        learning_path = self.path_planner.normalize(
            payload.get("learning_path"), profile=profile, papers=papers, references=references
        )
        evidence_claims = self._normalize_evidence_claims(
            payload.get("evidence_claims"),
            references,
            stages,
        )
        try:
            return DomainOnboardingOutput(
                domain=str(payload.get("domain") or plan.normalized_domain or request.query),
                text=str(payload.get("text") or f"{plan.normalized_domain} 领域入门方案。"),
                learner_profile=profile,
                prerequisites=prerequisites,
                development_stages=stages,
                current_landscape=CurrentLandscape(
                    problems=problems,
                    subdirections=subdirections,
                ),
                learning_path=learning_path,
                papers=[SelectedPaper.from_ranked(paper) for paper in papers],
                evidence_claims=evidence_claims,
            )
        except ValidationError as error:
            raise GenerationError(f"generated output failed validation: {error}") from error

    def _normalize_evidence_claims(
        self,
        value: object,
        references: dict[str, PaperReference],
        stages: list[DevelopmentStage],
    ) -> list[EvidenceClaim]:
        items = value if isinstance(value, list) else []
        claims: list[EvidenceClaim] = []
        allowed_support_types = {
            "abstract_explicit",
            "metadata_inference",
            "background_synthesis",
        }
        for item in items:
            if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
                continue
            paper_ids = self._valid_ids(item.get("supporting_paper_ids"), references)
            support_type = str(item.get("support_type") or "background_synthesis")
            if support_type not in allowed_support_types:
                support_type = "background_synthesis"
            claims.append(
                EvidenceClaim(
                    claim_id=item.get("claim_id"),
                    claim=str(item["claim"]),
                    supporting_paper_ids=paper_ids,
                    support_type=support_type,
                )
            )
        if claims:
            return claims
        return [
            EvidenceClaim(
                claim=stage.summary or stage.name,
                supporting_paper_ids=stage.related_paper_ids,
                support_type="background_synthesis",
            )
            for stage in stages
            if stage.related_paper_ids and (stage.summary.strip() or stage.name.strip())
        ]

    def _normalize_prerequisites(
        self,
        value: object,
        references: dict[str, PaperReference],
    ) -> list[Prerequisite]:
        items = value if isinstance(value, list) else []
        results: list[Prerequisite] = []
        for item in items:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            ids = self._valid_ids(item.get("related_paper_ids"), references)
            results.append(
                Prerequisite(
                    prerequisite_id=item.get("prerequisite_id"),
                    name=str(item["name"]),
                    why_needed=str(item.get("why_needed") or ""),
                    key_points=self._strings(item.get("key_points")),
                    related_paper_ids=ids,
                )
            )
        return results

    def _normalize_stages(
        self,
        value: object,
        references: dict[str, PaperReference],
        prerequisites: list[Prerequisite],
        papers: list[RankedPaper],
    ) -> list[DevelopmentStage]:
        items = value if isinstance(value, list) else []
        prerequisite_ids = {item.prerequisite_id for item in prerequisites if item.prerequisite_id}
        results: list[DevelopmentStage] = []
        for index, item in enumerate(items):
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            ids = self._valid_ids(item.get("related_paper_ids"), references)
            if not ids and papers:
                ids = [papers[min(index, len(papers) - 1)].paper_id]
            stage_prereqs = [
                str(item_id) for item_id in self._as_list(item.get("prerequisite_ids"))
                if str(item_id) in prerequisite_ids
            ]
            refs = [references[paper_id].model_copy(deep=True) for paper_id in ids]
            results.append(
                DevelopmentStage(
                    stage_id=item.get("stage_id"),
                    name=str(item["name"]),
                    summary=str(item.get("summary") or ""),
                    motivation=str(item.get("motivation") or ""),
                    representative_papers=refs,
                    core_concepts=self._strings(item.get("core_concepts")),
                    main_techniques=self._strings(item.get("main_techniques")),
                    open_problems=self._strings(item.get("open_problems")),
                    related_paper_ids=ids,
                    prerequisite_ids=stage_prereqs,
                )
            )
        return results

    @staticmethod
    def _as_list(value: object) -> list[object]:
        if isinstance(value, list):
            return value
        if value is None or value == "":
            return []
        return [value]

    @classmethod
    def _strings(cls, value: object) -> list[str]:
        normalized: list[str] = []
        for item in cls._as_list(value):
            if isinstance(item, dict):
                item = item.get("name") or item.get("title") or item.get("label") or ""
            if isinstance(item, (list, tuple, set)):
                continue
            text = str(item).strip()
            if text:
                normalized.append(text)
        return list(dict.fromkeys(normalized))

    @classmethod
    def _valid_ids(cls, value: object, references: dict[str, PaperReference]) -> list[str]:
        return list(dict.fromkeys(str(item) for item in cls._as_list(value) if str(item) in references))
    GenerationResult,
