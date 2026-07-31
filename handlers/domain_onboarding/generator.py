"""仅基于已验证候选论文生成结构化领域入门内容。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

from pydantic import ValidationError

from .config import DomainOnboardingConfig
from .learning_bindings import LearningPaperBinder
from .llm import StructuredLLMError, invoke_json
from .relations import SemanticRelationResolver
from .schemas import (
    CurrentLandscape,
    DevelopmentStage,
    DomainOnboardingOutput,
    DomainOnboardingRequest,
    DomainResearchPlan,
    EvidenceClaim,
    GenerationResult,
    LearnerProfile,
    LandscapeProblem,
    LearningStep,
    ModelCallStats,
    PaperReference,
    Prerequisite,
    QualityIssue,
    RankedPaper,
    SelectedPaper,
    StageBreakthrough,
    SubdirectionDetail,
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
        week_windows = self._week_windows(profile.time_budget_weeks, len(self.stage_names))
        paper_by_id = {paper.paper_id: paper for paper in papers}
        for index, raw in enumerate(provided[: len(self.stage_names)], start=1):
            if not isinstance(raw, dict):
                continue
            ids = self._valid_ids(raw.get("paper_ids"), references)
            desired_roles = {
                1: {"survey", "foundational"},
                2: {"survey", "foundational", "method"},
                3: {"foundational", "method"},
                4: {"method", "evaluation", "application"},
                5: {"frontier", "evaluation", "method"},
            }[index]
            role_fit = [paper_id for paper_id in ids if paper_by_id[paper_id].paper_role in desired_roles]
            if ids:
                ids = role_fit
            if index <= 2:
                ids = [
                    paper_id
                    for paper_id in ids
                    if paper_by_id[paper_id].paper_role not in {"application", "frontier"}
                ]
            if not ids and papers:
                eligible = [
                    paper
                    for paper in papers
                    if paper.paper_role in desired_roles
                ]
                used_ids = {
                    paper_id for step in results for paper_id in step.paper_ids
                }
                preferred_roles = {
                    1: ("foundational", "survey"),
                    2: ("foundational", "survey", "method"),
                    3: ("method", "foundational"),
                    4: ("evaluation", "method", "application"),
                    5: ("frontier", "method", "evaluation", "survey"),
                }[index]
                chosen = sorted(
                    eligible or papers,
                    key=lambda paper: (
                        paper.paper_id in used_ids,
                        preferred_roles.index(paper.paper_role)
                        if paper.paper_role in preferred_roles
                        else len(preferred_roles),
                        -paper.final_score,
                    ),
                )
                ids = [chosen[0].paper_id]
            activities = self._strings(raw.get("activities"))
            criteria = self._strings(raw.get("completion_criteria"))
            goal = str(raw.get("goal") or "")
            topics = self._strings(raw.get("topics"))
            outcome = str(raw.get("expected_outcome") or "")
            deliverables = self._strings(raw.get("deliverables"))
            reproducibility = self._strings(raw.get("reproducibility_checklist"))
            evaluation_metrics = self._strings(raw.get("evaluation_metrics"))
            start_week, end_week = week_windows[index - 1]
            estimated_hours = self._positive_int(raw.get("estimated_hours"))
            if estimated_hours is None and start_week is not None and end_week is not None:
                estimated_hours = max(4, (end_week - start_week + 1) * 6)
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
                    start_week=start_week,
                    end_week=end_week,
                    estimated_hours=estimated_hours,
                    milestone=str(raw.get("milestone") or ""),
                    deliverables=deliverables,
                    reproducibility_checklist=reproducibility,
                    evaluation_metrics=evaluation_metrics,
                )
            )
        return results

    @staticmethod
    def _week_windows(
        total_weeks: int | None,
        step_count: int,
    ) -> list[tuple[int | None, int | None]]:
        if total_weeks is None:
            return [(None, None)] * step_count
        windows = []
        for index in range(step_count):
            start = index * total_weeks // step_count + 1
            end = max(start, (index + 1) * total_weeks // step_count)
            windows.append((start, min(total_weeks, end)))
        return windows

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            ]
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
        self.paper_binder = LearningPaperBinder()
        self.relation_resolver = SemanticRelationResolver(
            semantic_threshold=config.coverage_similarity_threshold
        )

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if 1800 <= parsed <= 2100 else None

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

    def generate_incrementally(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        on_section: Callable[[str, dict[str, Any], list[str]], None],
    ) -> GenerationResult:
        """Generate complete JSON sections independently and publish only validated boundaries."""
        payload: dict[str, Any] = {}
        stats = ModelCallStats()
        sections = {
            "development": ("development_ready", ["domain", "text", "prerequisites", "development_stages", "paper_guidance", "evidence_claims"]),
            "landscape": ("landscape_ready", ["current_landscape", "evidence_claims"]),
            "learning_path": ("learning_path_ready", ["learning_path", "evidence_claims"]),
        }

        def apply_section(section: str, section_payload: dict[str, Any], section_stats: ModelCallStats) -> None:
            event_name, keys = sections[section]
            for key in keys:
                if key == "evidence_claims":
                    payload.setdefault(key, []).extend(section_payload.get(key) or [])
                elif key in section_payload:
                    payload[key] = section_payload[key]
            self._add_stats(stats, section_stats)
            normalized = self._normalize(payload, request, profile, plan, papers)
            if section == "development":
                data = {
                    "domain": normalized.domain,
                    "text": normalized.text,
                    "learner_profile": normalized.learner_profile.model_dump(mode="json"),
                    "prerequisites": [item.model_dump(mode="json") for item in normalized.prerequisites],
                    "development_stages": [item.model_dump(mode="json") for item in normalized.development_stages],
                    "papers": [item.model_dump(mode="json") for item in normalized.papers],
                }
                paths = ["domain", "text", "learner_profile", "prerequisites", "development_stages", "papers"]
            elif section == "landscape":
                data = {"current_landscape": normalized.current_landscape.model_dump(mode="json")}
                paths = ["current_landscape"]
            else:
                data = {"learning_path": [item.model_dump(mode="json") for item in normalized.learning_path]}
                paths = ["learning_path"]
            on_section(event_name, data, paths)

        development_payload, development_stats = self._call_section(
            "development", request, profile, plan, papers, payload
        )
        apply_section("development", development_payload, development_stats)
        completed_snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="onboarding-content") as executor:
            futures = {
                executor.submit(
                    self._call_section,
                    section,
                    request,
                    profile,
                    plan,
                    papers,
                    completed_snapshot,
                ): section
                for section in ("landscape", "learning_path")
            }
            completed_sections = {}
            failed_sections: dict[str, GenerationError] = {}
            for future in as_completed(futures):
                section = futures[future]
                try:
                    completed_sections[section] = future.result()
                except GenerationError as error:
                    failed_sections[section] = error
            for section in ("landscape", "learning_path"):
                if section in completed_sections:
                    section_payload, section_stats = completed_sections[section]
                    apply_section(section, section_payload, section_stats)
            if failed_sections:
                for error in failed_sections.values():
                    self._add_stats(stats, error.stats)
                failed_section = next(
                    section
                    for section in ("landscape", "learning_path")
                    if section in failed_sections
                )
                failed_error = failed_sections[failed_section]
                raise GenerationError(
                    f"{failed_section} section generation failed: {failed_error}",
                    stats=stats,
                ) from failed_error
        return GenerationResult(
            output=self._normalize(payload, request, profile, plan, papers), stats=stats
        )

    @staticmethod
    def _add_stats(total: ModelCallStats, item: ModelCallStats) -> None:
        total.duration_ms += item.duration_ms
        total.model_calls += item.model_calls
        total.prompt_tokens += item.prompt_tokens
        total.completion_tokens += item.completion_tokens
        total.total_tokens += item.total_tokens
        total.usage_reported = total.usage_reported or item.usage_reported

    def _call_section(
        self,
        section: str,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        completed: dict[str, Any],
    ) -> tuple[dict[str, Any], ModelCallStats]:
        instructions = {
            "development": (
                "Return domain, text, exactly 3 prerequisites, exactly 3 chronological development_stages, "
                "paper_guidance and evidence_claims. historical_period must be real calendar years or eras, never learner weeks. "
                "Use start_year/end_year when known; stage 1 has empty transition, later stages explain the causal transition. "
                "Every stage must include one or more breakthroughs. Each breakthrough has breakthrough_id, name, description, "
                "supporting_paper_ids, enabled_capabilities and limitation_problem_ids. Use only allowed paper IDs; "
                "limitation_problem_ids may stay empty until the landscape section is resolved."
            ),
            "landscape": (
                "Return current_landscape and evidence_claims. Include 3 problems and 3-5 subdirections. "
                "Each problem includes related papers, emerged_in_stage_id, affected_stage_ids and related_subdirection_ids. "
                "Each subdirection includes related papers, emerged_in_stage_id and addresses_problem_ids. "
                "Use research_plan.expected_subdirections as the intended domain taxonomy; do not replace it with generic "
                "paper roles such as survey, method, evaluation, or application. Only emit a relation ID when the prose or "
                "shared paper evidence supports that relation."
            ),
            "learning_path": (
                "Return five learning_path steps and evidence_claims. Suggest papers according to the actual learning task: "
                "survey/foundational work for concepts, foundational/classic methods for architecture, method papers for improvements, "
                "an implementable method as the experiment baseline, evaluation papers only for learning evaluation, and recent "
                "problem-driven methods for the frontier. An evaluation or application paper must never be the sole baseline paper. "
                "Each step needs deliverables; the experiment step also needs reproducibility_checklist and evaluation_metrics."
            ),
        }
        examples = {
            "development": (
                '{"domain":"domain","text":"summary","prerequisites":[{"name":"foundation",'
                '"why_needed":"reason","key_points":["concept"],"related_paper_ids":[]}],'
                '"development_stages":[{"stage_id":"stage_1","name":"stage","historical_period":"2020-2022",'
                '"summary":"summary","related_paper_ids":["paper_1"],"breakthroughs":['
                '{"breakthrough_id":"breakthrough_1","name":"advance","description":"what changed",'
                '"supporting_paper_ids":["paper_1"],"enabled_capabilities":["capability"],'
                '"limitation_problem_ids":[]}]}],"paper_guidance":[],"evidence_claims":[]}'
            ),
            "landscape": (
                '{"current_landscape":{"problems":["problem"],"subdirections":["direction"],'
                '"problem_details":[],"subdirection_details":[]},"evidence_claims":[]}'
            ),
            "learning_path": (
                '{"learning_path":[{"step":"1","goal":"goal","topics":["topic"],'
                '"paper_ids":[],"activities":["activity"],"completion_criteria":["criterion"],'
                '"expected_outcome":"outcome"}],"evidence_claims":[]}'
            ),
        }
        system_prompt = (
            "You generate one section of a grounded domain onboarding result. Return one JSON object only. "
            f"Write explanatory prose in {request.language}; preserve English paper titles and technical terms. "
            "Use only allowed paper IDs and stage IDs. "
            + instructions[section]
            + " Use these exact top-level keys. Example JSON shape: "
            + examples[section]
        )
        user_payload = {
            "request": request.model_dump(mode="json"),
            "learner_profile": profile.model_dump(mode="json"),
            "research_plan": plan.model_dump(mode="json"),
            "allowed_papers": [self._paper_prompt_payload(paper) for paper in papers],
            "completed_sections": completed,
        }
        try:
            payload, stats = invoke_json(
                self.model,
                system_prompt=system_prompt,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                max_tokens={
                    "development": self.config.generation_development_max_tokens,
                    "landscape": self.config.generation_landscape_max_tokens,
                    "learning_path": self.config.generation_learning_path_max_tokens,
                }[section],
                timeout_seconds=self.config.generation_section_timeout_seconds,
            )
            try:
                completed = self._complete_section_payload(section, payload, papers)
            except GenerationError as error:
                error.stats = stats
                raise
            return completed, stats
        except StructuredLLMError as error:
            raise GenerationError(str(error), stats=error.stats) from error

    def _complete_section_payload(
        self,
        section: str,
        payload: dict[str, Any],
        papers: list[RankedPaper],
    ) -> dict[str, Any]:
        expected = {
            "development": ("domain", "prerequisites", "development_stages"),
            "landscape": ("current_landscape",),
            "learning_path": ("learning_path",),
        }[section]
        candidates = []
        for key in (section, "result", "data", "output"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        candidates.append(payload)
        selected = next(
            (
                candidate
                for candidate in candidates
                if any(key in candidate for key in expected)
            ),
            payload,
        )
        completed = dict(selected)
        self._validate_section_payload(section, completed, papers)
        return completed

    def _validate_section_payload(
        self,
        section: str,
        payload: dict[str, Any],
        papers: list[RankedPaper],
    ) -> None:
        if section == "development":
            prerequisites = payload.get("prerequisites")
            stages = payload.get("development_stages")
            if not str(payload.get("domain") or "").strip():
                raise GenerationError("development section is missing domain")
            if not isinstance(prerequisites, list):
                raise GenerationError("development section is missing prerequisites")
            if not isinstance(stages, list):
                raise GenerationError("development section is missing development stages")
            allowed_ids = {paper.paper_id for paper in papers}
            for index, stage in enumerate(stages):
                breakthroughs = stage.get("breakthroughs") if isinstance(stage, dict) else None
                if not isinstance(breakthroughs, list) or not breakthroughs:
                    raise GenerationError(
                        f"development stage {index} is missing breakthroughs"
                    )
                for breakthrough in breakthroughs:
                    if not isinstance(breakthrough, dict):
                        raise GenerationError(
                            f"development stage {index} has an invalid breakthrough"
                        )
                    supporting_ids = set(
                        self._strings(breakthrough.get("supporting_paper_ids"))
                    )
                    if not str(breakthrough.get("name") or "").strip() or not str(
                        breakthrough.get("description") or ""
                    ).strip():
                        raise GenerationError(
                            f"development stage {index} has an incomplete breakthrough"
                        )
                    if not supporting_ids or not supporting_ids <= allowed_ids:
                        raise GenerationError(
                            f"development stage {index} breakthrough has invalid paper evidence"
                        )
            return
        if section == "landscape":
            landscape = payload.get("current_landscape")
            if not isinstance(landscape, dict):
                raise GenerationError("landscape section is missing current_landscape")
            return
        steps = payload.get("learning_path")
        if not isinstance(steps, list):
            raise GenerationError("learning path section is missing learning_path")

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
            f"You generate a beginner-friendly domain onboarding plan in {request.language}. Return one JSON object only. "
            "You MUST use only paper_id values from allowed_papers; never invent or modify paper metadata. "
            "Output fields: domain, text, prerequisites, development_stages, current_landscape, learning_path. "
            "Each prerequisite has name, why_needed, key_points, related_paper_ids. Each development stage has "
            "stage_id, name, historical_period, start_year, end_year, summary, motivation, transition_from_previous, related_paper_ids, prerequisite_ids, "
            "core_concepts, main_techniques, open_problems and breakthroughs. Each breakthrough has breakthrough_id, name, "
            "description, supporting_paper_ids, enabled_capabilities and limitation_problem_ids. Use short stable IDs such as stage_1. "
            "current_landscape has problems:list[str], subdirections:list[str], problem_details and subdirection_details. "
            "Each problem detail has name, description, related_paper_ids and related_stage_ids. Each subdirection detail "
            "has name, description, why_it_matters, research_questions, related_paper_ids and related_stage_ids. "
            "Ground every landscape detail in allowed paper IDs and stage IDs. "
            "Use research_plan.expected_subdirections as the domain taxonomy instead of generic paper-role categories. "
            "Connect stages, breakthroughs, problems and subdirections only when explicit prose or shared paper evidence supports the edge. "
            "Each learning step has step, goal, topics, paper_ids, "
            "activities, completion_criteria, expected_outcome. Produce 3 development stages and 3-5 subdirections. "
            "Also output evidence_claims:[{claim,supporting_paper_ids,support_type}]. Important technical or historical "
            "claims must cite allowed paper IDs. support_type is abstract_explicit, metadata_inference, or background_synthesis. "
            "Evidence claims must collectively cover every development stage and every current-landscape problem/subdirection. "
            "Use abstract_explicit only when every cited paper has a non-empty abstract and directly supports the claim; "
            "metadata_inference and background_synthesis are weak evidence and must not be phrased as proven facts. "
            "Prefer concise abstract_explicit claims copied faithfully from a paper abstract, and retain the paper's exact "
            "English technical terms in parentheses so cross-language evidence can be reproduced. Use one paper per claim "
            "unless the claim truly synthesizes multiple papers. "
            "Also output paper_guidance:[{paper_id,contribution,reading_focus:list[str]}] for every core or recommended paper. "
            "contribution explains why the paper belongs at this learning stage; reading_focus contains 1-3 concrete reading targets. "
            "Development stages describe field history, not the learner schedule. historical_period uses calendar years/eras and never weeks. Every "
            "stage after the first must explain how the earlier limitation motivated the next stage. "
            "Use five ordered learning steps: 基础准备, 核心概念, 代表方法与论文, 工具、数据集与基线实验, 前沿问题与研究切入. "
            "Paper IDs in learning steps are suggestions that code will verify against the learning task. Use surveys/foundational work "
            "for concepts, foundational/classic methods for architecture, method papers for improvements, an implementable method for "
            "baseline reproduction, evaluation papers for evaluation, and recent problem-driven methods for the frontier. Never use an "
            "evaluation framework or a narrow application as the sole baseline or frontier entry. "
            "Each learning step includes estimated_hours and milestone; its content must fit the learner time budget. "
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
                timeout_seconds=(
                    self.config.repair_timeout_seconds
                    if previous_output is not None
                    else self.config.generation_timeout_seconds
                ),
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
            "reading_priority": paper.reading_priority,
            "is_canonical": paper.is_canonical,
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
        guidance = self._paper_guidance(payload.get("paper_guidance"))
        references = {
            paper.paper_id: PaperReference(
                paper_id=paper.paper_id,
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                url=paper.url,
                contribution=(
                    guidance.get(paper.paper_id, {}).get("contribution")
                    or ""
                ),
                reading_focus=(
                    self._strings(guidance.get(paper.paper_id, {}).get("reading_focus"))
                ),
                reading_priority=paper.reading_priority,
                is_canonical=paper.is_canonical,
            )
            for paper in papers
        }
        prerequisites = self._normalize_prerequisites(payload.get("prerequisites"), references)
        stages = self._normalize_stages(payload.get("development_stages"), references, prerequisites, papers)
        landscape_raw = payload.get("current_landscape") if isinstance(payload.get("current_landscape"), dict) else {}
        landscape = self._normalize_landscape(
            landscape_raw,
            plan,
            stages,
            references,
        )
        landscape = self.relation_resolver.resolve(stages, landscape, papers)
        learning_path = self.path_planner.normalize(
            payload.get("learning_path"), profile=profile, papers=papers, references=references
        )
        learning_path = self.paper_binder.bind(
            learning_path,
            papers,
            stages,
            landscape,
            references,
            language=request.language,
        )
        evidence_claims = self._normalize_evidence_claims(
            payload.get("evidence_claims"),
            references,
            stages,
        )
        try:
            text = str(payload.get("text") or "").strip()
            display_domain = self._display_domain(
                request,
                str(payload.get("domain") or plan.normalized_domain or request.query),
            )
            selected_papers = []
            for paper in papers:
                reference = references[paper.paper_id]
                selected_papers.append(
                    SelectedPaper.from_ranked(paper).model_copy(
                        update={
                            "contribution": reference.contribution,
                            "reading_focus": list(reference.reading_focus),
                        }
                    )
                )
            return DomainOnboardingOutput(
                language=request.language,
                domain=display_domain,
                text=text,
                learner_profile=profile,
                research_plan=plan,
                prerequisites=prerequisites,
                development_stages=stages,
                current_landscape=landscape,
                learning_path=learning_path,
                papers=selected_papers,
                evidence_claims=evidence_claims,
                reproducibility={
                    "policy_version": self.config.policy_version,
                    "search_queries": plan.search_queries,
                    "retrieval_sources": sorted({paper.source for paper in papers}),
                    "selected_paper_ids": [paper.paper_id for paper in papers],
                },
            )
        except ValidationError as error:
            raise GenerationError(f"generated output failed validation: {error}") from error

    @staticmethod
    def _display_domain(request: DomainOnboardingRequest, candidate: str) -> str:
        if request.language != "zh-CN":
            return candidate
        mappings = (
            (r"检索增强|\brag\b", "检索增强生成（RAG）"),
            (r"多模态", "多模态大模型"),
            (r"多智能体辩论", "多智能体辩论"),
            (r"图神经网络", "图神经网络"),
            (r"扩散模型", "扩散模型"),
            (r"幻觉", "大模型幻觉检测"),
        )
        for pattern, display in mappings:
            if re.search(pattern, request.query, re.IGNORECASE):
                return display
        return candidate

    def _normalize_evidence_claims(
        self,
        value: object,
        references: dict[str, PaperReference],
        stages: list[DevelopmentStage],
    ) -> list[EvidenceClaim]:
        items = value if isinstance(value, list) else []
        claims: list[EvidenceClaim] = []
        claim_indexes: dict[str, int] = {}
        allowed_support_types = {
            "abstract_explicit",
            "metadata_inference",
            "background_synthesis",
        }
        for item in items:
            if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
                continue
            normalized_claim = str(item["claim"]).strip()
            paper_ids = self._valid_ids(item.get("supporting_paper_ids"), references)
            if normalized_claim in claim_indexes:
                existing = claims[claim_indexes[normalized_claim]]
                existing.supporting_paper_ids = list(
                    dict.fromkeys([*existing.supporting_paper_ids, *paper_ids])
                )
                continue
            support_type = str(item.get("support_type") or "background_synthesis")
            if support_type not in allowed_support_types:
                support_type = "background_synthesis"
            claims.append(
                EvidenceClaim(
                    claim_id=item.get("claim_id"),
                    claim=normalized_claim,
                    supporting_paper_ids=paper_ids,
                    support_type=support_type,
                )
            )
            claim_indexes[normalized_claim] = len(claims) - 1
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

    def _normalize_landscape(
        self,
        value: dict[str, Any],
        plan: DomainResearchPlan,
        stages: list[DevelopmentStage],
        references: dict[str, PaperReference],
    ) -> CurrentLandscape:
        problem_names = self._strings(value.get("problems"))
        subdirection_names = (
            self._strings(value.get("subdirections"))
            or list(plan.expected_subdirections)
        )
        stage_aliases: dict[str, str] = {}
        for stage in stages:
            if not stage.stage_id:
                continue
            canonical = str(stage.stage_id)
            for alias in (
                canonical,
                stage.name,
                str(stage.sequence),
                f"stage_{stage.sequence}",
                f"stage-{stage.sequence}",
                f"阶段 {stage.sequence}",
            ):
                stage_aliases[alias.strip().lower()] = canonical
        problem_details = self._landscape_problems(
            value.get("problem_details"),
            problem_names,
            references,
            stage_aliases,
        )
        subdirection_details = self._subdirection_details(
            value.get("subdirection_details"),
            subdirection_names,
            references,
            stage_aliases,
        )
        subdirection_aliases = {
            alias: str(item.subdirection_id)
            for item in subdirection_details
            if item.subdirection_id
            for alias in (str(item.subdirection_id), item.name)
        }
        problem_aliases = {
            alias: str(item.problem_id)
            for item in problem_details
            if item.problem_id
            for alias in (str(item.problem_id), item.name)
        }
        for problem in problem_details:
            problem.related_subdirection_ids = list(dict.fromkeys(
                subdirection_aliases[item]
                for item in problem.related_subdirection_ids
                if item in subdirection_aliases
            ))
        for subdirection in subdirection_details:
            subdirection.addresses_problem_ids = list(dict.fromkeys(
                problem_aliases[item]
                for item in subdirection.addresses_problem_ids
                if item in problem_aliases
            ))
        return CurrentLandscape(
            problems=problem_names or [item.name for item in problem_details],
            subdirections=(
                subdirection_names or [item.name for item in subdirection_details]
            ),
            problem_details=problem_details,
            subdirection_details=subdirection_details,
        )

    def _landscape_problems(
        self,
        value: object,
        names: list[str],
        references: dict[str, PaperReference],
        stage_aliases: dict[str, str],
    ) -> list[LandscapeProblem]:
        raw_items = value if isinstance(value, list) else []
        by_name = {
            str(item.get("name") or "").strip(): item
            for item in raw_items
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        ordered_names = list(dict.fromkeys([*names, *by_name]))
        results = []
        for name in ordered_names:
            raw = by_name.get(name, {})
            paper_ids = self._valid_ids(raw.get("related_paper_ids"), references)
            results.append(
                LandscapeProblem(
                    problem_id=raw.get("problem_id"),
                    name=name,
                    description=str(raw.get("description") or ""),
                    related_paper_ids=paper_ids,
                    related_stage_ids=self._valid_stage_ids(
                        raw.get("related_stage_ids"), stage_aliases
                    ),
                    emerged_in_stage_id=self._first_valid_stage_id(raw.get("emerged_in_stage_id"), stage_aliases),
                    affected_stage_ids=self._valid_stage_ids(raw.get("affected_stage_ids"), stage_aliases),
                    related_subdirection_ids=self._strings(raw.get("related_subdirection_ids")),
                )
            )
        return results

    def _subdirection_details(
        self,
        value: object,
        names: list[str],
        references: dict[str, PaperReference],
        stage_aliases: dict[str, str],
    ) -> list[SubdirectionDetail]:
        raw_items = value if isinstance(value, list) else []
        by_name = {
            str(item.get("name") or "").strip(): item
            for item in raw_items
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        ordered_names = list(dict.fromkeys([*names, *by_name]))
        results = []
        for name in ordered_names:
            raw = by_name.get(name, {})
            paper_ids = self._valid_ids(raw.get("related_paper_ids"), references)
            results.append(
                SubdirectionDetail(
                    subdirection_id=raw.get("subdirection_id"),
                    name=name,
                    description=str(raw.get("description") or ""),
                    why_it_matters=str(raw.get("why_it_matters") or ""),
                    research_questions=self._strings(raw.get("research_questions")),
                    related_paper_ids=paper_ids,
                    related_stage_ids=self._valid_stage_ids(
                        raw.get("related_stage_ids"), stage_aliases
                    ),
                    emerged_in_stage_id=self._first_valid_stage_id(raw.get("emerged_in_stage_id"), stage_aliases),
                    addresses_problem_ids=self._strings(raw.get("addresses_problem_ids")),
                )
            )
        return results

    def _valid_stage_ids(
        self,
        value: object,
        stage_aliases: dict[str, str],
    ) -> list[str]:
        return list(
            dict.fromkeys(
                stage_aliases[str(item).strip().lower()]
                for item in self._as_list(value)
                if str(item).strip().lower() in stage_aliases
            )
        )

    def _first_valid_stage_id(self, value: object, aliases: dict[str, str]) -> str | None:
        values = self._valid_stage_ids(value, aliases)
        return values[0] if values else None

    @staticmethod
    def _paper_guidance(value: object) -> dict[str, dict[str, object]]:
        items = value if isinstance(value, list) else []
        return {
            str(item.get("paper_id")): item
            for item in items
            if isinstance(item, dict) and str(item.get("paper_id") or "").strip()
        }

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
            if not stage_prereqs and prerequisites:
                stage_prereqs = [
                    str(prerequisites[min(index, len(prerequisites) - 1)].prerequisite_id)
                ]
            breakthroughs = []
            for raw_breakthrough in self._as_list(item.get("breakthroughs")):
                if not isinstance(raw_breakthrough, dict):
                    continue
                name = str(raw_breakthrough.get("name") or "").strip()
                description = str(
                    raw_breakthrough.get("description") or ""
                ).strip()
                if not name or not description:
                    continue
                supporting_ids = self._valid_ids(
                    raw_breakthrough.get("supporting_paper_ids"), references
                )
                if not supporting_ids:
                    continue
                breakthroughs.append(
                    StageBreakthrough(
                        breakthrough_id=raw_breakthrough.get("breakthrough_id"),
                        name=name,
                        description=description,
                        supporting_paper_ids=supporting_ids,
                        enabled_capabilities=self._strings(
                            raw_breakthrough.get("enabled_capabilities")
                        ),
                        limitation_problem_ids=self._strings(
                            raw_breakthrough.get("limitation_problem_ids")
                        ),
                    )
                )
            if not breakthroughs:
                raise GenerationError(
                    f"development stage {index} has no grounded breakthrough"
                )
            ids = list(
                dict.fromkeys(
                    [
                        *ids,
                        *(
                            paper_id
                            for breakthrough in breakthroughs
                            for paper_id in breakthrough.supporting_paper_ids
                        ),
                    ]
                )
            )
            refs = [references[paper_id].model_copy(deep=True) for paper_id in ids]
            results.append(
                DevelopmentStage(
                    stage_id=item.get("stage_id"),
                    sequence=index + 1,
                    name=str(item["name"]),
                    period=str(item.get("historical_period") or item.get("period") or ""),
                    historical_period=str(item.get("historical_period") or item.get("period") or ""),
                    start_year=self._positive_int(item.get("start_year")),
                    end_year=self._positive_int(item.get("end_year")),
                    summary=str(item.get("summary") or ""),
                    motivation=str(item.get("motivation") or ""),
                    transition_from_previous=str(
                        item.get("transition_from_previous") or ""
                    ),
                    representative_papers=refs,
                    core_concepts=self._strings(item.get("core_concepts")),
                    main_techniques=self._strings(item.get("main_techniques")),
                    open_problems=self._strings(item.get("open_problems")),
                    breakthroughs=breakthroughs,
                    related_paper_ids=ids,
                    prerequisite_ids=stage_prereqs,
                )
            )
        for index, stage in enumerate(results):
            if index == 0:
                stage.previous_stage_id = None
                stage.transition_from_previous = ""
                continue
            previous = results[index - 1]
            stage.previous_stage_id = previous.stage_id
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
