"""仅基于已验证候选论文生成结构化领域入门内容。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

from pydantic import ValidationError

from .config import DomainOnboardingConfig
from .execution import current_cancel_event
from .learning_bindings import LearningPaperBinder
from .llm import StructuredLLMError, invoke_json
from .model_routing import routing_snapshot, run_with_model_route
from .profile import standard_novice_profile
from .prompts import (
    development_foundation_prompt,
    development_stage_content_prompt,
    development_stage_planning_prompt,
    full_generation_system_prompt,
    section_system_prompt,
)
from .relations import SemanticRelationResolver
from .response_contracts import (
    DEVELOPMENT_FOUNDATION_CONTRACT,
    DEVELOPMENT_STAGE_CONTRACT,
    FULL_ONBOARDING_CONTRACT,
    REPAIR_PATCH_CONTRACT,
    SECTION_CONTRACTS,
    STAGE_PLANNING_CONTRACT,
)
from .schemas import (
    ConceptDetail,
    CurrentLandscape,
    DevelopmentStage,
    DomainOnboardingOutput,
    DomainOnboardingRequest,
    DomainResearchPlan,
    DevelopmentStageResearchPlan,
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
    TechniqueDetail,
    is_internal_landscape_label,
)
from .structured_response import (
    ResponseContract,
    StructuredResponseError,
    adapt_structured_response,
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
        # The public signature remains compatible, but scheduling is deliberately
        # independent of any learner profile in the standard beginner route.
        del profile
        week_windows = [(None, None)] * len(self.stage_names)
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
    def __init__(
        self,
        model: Any,
        config: DomainOnboardingConfig,
        *,
        section_models: dict[str, Any] | None = None,
        repair_model: Any | None = None,
    ):
        self.model = model
        self.config = config
        self.section_models = section_models or {}
        self.repair_model = repair_model or model
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
        on_delta: Callable[[str, str], None] | None = None,
    ) -> GenerationResult:
        payload, stats = self._call_model(
            request, profile, plan, papers, on_delta=on_delta
        )
        try:
            output = self._normalize(payload, request, profile, plan, papers)
        except GenerationError as error:
            error.stats = stats
            raise
        return GenerationResult(output=output, stats=stats)

    def plan_development_research(
        self,
        request: DomainOnboardingRequest,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        on_delta: Callable[[str, str], None] | None = None,
    ) -> tuple[list[DevelopmentStageResearchPlan], ModelCallStats]:
        """Create a chronological outline before any stage-specific retrieval."""

        user_payload = {
            "domain": plan.normalized_domain,
            "translated_domain": plan.translated_domain,
            "expanded_terms": plan.expanded_terms,
            "research_perspectives": [
                perspective.model_dump(mode="json") for perspective in plan.perspectives
            ],
            "seed_papers": [
                {
                    "paper_id": paper.paper_id,
                    "title": paper.title,
                    "year": paper.year,
                    "paper_role": paper.paper_role,
                }
                for paper in papers
            ],
        }
        section_model = self.section_models.get(
            "stage_planning", self.section_models.get("development", self.model)
        )

        def generate_candidate(candidate: Any, timeout_seconds: float | None):
            payload, stats = invoke_json(
                candidate,
                system_prompt=development_stage_planning_prompt(request.language),
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                timeout_seconds=timeout_seconds,
                on_delta=on_delta,
                stream_stage="stage_planning",
                contract=STAGE_PLANNING_CONTRACT,
            )
            raw_plans = payload.get("development_stage_plans")
            if not isinstance(raw_plans, list):
                raise GenerationError("stage planning is missing development_stage_plans")
            try:
                stages = [
                    DevelopmentStageResearchPlan.model_validate(
                        {
                            **item,
                            "search_queries": item.get("search_queries")
                            or (
                                [item["search_query"]]
                                if str(item.get("search_query") or "").strip()
                                else []
                            ),
                        }
                    )
                    for item in raw_plans[: self.config.max_development_stage_plans]
                    if isinstance(item, dict)
                ]
            except ValidationError as error:
                raise GenerationError(f"stage planning failed validation: {error}") from error
            if len(stages) < 3 or [item.sequence for item in stages] != list(
                range(1, len(stages) + 1)
            ):
                raise GenerationError("stage planning must contain 3-4 consecutive stages")
            if any(index and not stage.transition_from_previous for index, stage in enumerate(stages)):
                raise GenerationError("later stage plans require an explicit transition")
            return stages, stats

        try:
            return run_with_model_route(
                section_model,
                generate_candidate,
                timeout_seconds=self.config.development_stage_planning_timeout_seconds,
            )
        except StructuredLLMError as error:
            raise GenerationError(str(error), stats=error.stats) from error

    def generate_incrementally(
        self,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        on_section: Callable[[str, dict[str, Any], list[str]], None],
        on_delta: Callable[[str, str], None] | None = None,
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
                    payload.setdefault(key, []).extend(
                        self._mapping_items(section_payload.get(key))
                    )
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

        staged_error: GenerationError | None = None
        try:
            if plan.development_stage_plans:
                development_payload, development_stats = self._call_staged_development(
                    request, plan, papers, on_delta
                )
            else:
                development_payload, development_stats = self._call_section(
                    "development", request, profile, plan, papers, payload, on_delta
                )
        except GenerationError as error:
            if not plan.development_stage_plans:
                raise GenerationError(
                    f"development section generation failed: {error}",
                    stats=error.stats,
                ) from error
            # Individual stage failures are converted into auditable degraded
            # stages inside ``_call_staged_development``.  Reaching this branch
            # therefore means the shared foundation or stage set is unusable;
            # retain the standard whole-section call as the last recovery path.
            staged_error = error
            try:
                development_payload, development_stats = self._call_section(
                    "development", request, profile, plan, papers, payload, on_delta
                )
            except GenerationError as fallback_error:
                self._add_stats(fallback_error.stats, error.stats)
                raise GenerationError(
                    "development section generation failed after staged and "
                    f"standard attempts: {fallback_error}",
                    stats=fallback_error.stats,
                ) from fallback_error
        if staged_error is not None:
            self._add_stats(development_stats, staged_error.stats)
        apply_section("development", development_payload, development_stats)
        completed_snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        with ThreadPoolExecutor(
            max_workers=self.config.generation_section_workers,
            thread_name_prefix="onboarding-content",
        ) as executor:
            futures = {
                executor.submit(
                    self._call_section,
                    section,
                    request,
                    profile,
                    plan,
                    papers,
                    completed_snapshot,
                    on_delta,
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
            # Development is the minimum useful onboarding artifact. If an
            # optional later section still fails after retries, retain and
            # deliver every validated section already produced; the quality
            # evaluator will expose the missing coverage as a warning.
            for error in failed_sections.values():
                self._add_stats(stats, error.stats)
        return GenerationResult(
            output=self._normalize(payload, request, profile, plan, papers), stats=stats
        )

    def _call_staged_development(
        self,
        request: DomainOnboardingRequest,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        on_delta: Callable[[str, str], None] | None,
    ) -> tuple[dict[str, Any], ModelCallStats]:
        """Generate foundations once and each researched stage in its own bounded call."""

        model = self.section_models.get("development", self.model)
        foundation_payload, foundation_stats = self._invoke_development_piece(
            model,
            system_prompt=development_foundation_prompt(request.language),
            user_payload={
                "domain": plan.normalized_domain,
                "allowed_papers": [
                    {
                        "paper_id": paper.paper_id,
                        "title": paper.title,
                        "paper_role": paper.paper_role,
                    }
                    for paper in papers
                ],
            },
            timeout_seconds=self.config.generation_development_foundation_timeout_seconds,
            stream_stage="development_foundation",
            on_delta=on_delta,
            contract=DEVELOPMENT_FOUNDATION_CONTRACT,
        )
        foundation = foundation_payload
        if not isinstance(foundation.get("prerequisites"), list):
            raise GenerationError(
                "development foundation is missing prerequisites",
                stats=foundation_stats,
            )

        paper_by_id = {paper.paper_id: paper for paper in papers}

        def generate_stage(stage_plan: DevelopmentStageResearchPlan):
            stage_papers = [
                paper_by_id[paper_id]
                for paper_id in stage_plan.selected_paper_ids
                if paper_id in paper_by_id
            ]
            if not stage_papers:
                raise GenerationError(
                    f"stage {stage_plan.stage_id} has no available researched papers"
                )
            raw, item_stats = self._invoke_development_piece(
                model,
                system_prompt=development_stage_content_prompt(request.language),
                user_payload={
                    "domain": plan.normalized_domain,
                    "stage_research_plan": stage_plan.model_dump(mode="json"),
                    "stage_papers": [
                        self._paper_prompt_payload(paper) for paper in stage_papers
                    ],
                },
                timeout_seconds=self.config.generation_development_stage_timeout_seconds,
                stream_stage="development_stage",
                on_delta=on_delta,
                contract=DEVELOPMENT_STAGE_CONTRACT,
            )
            unwrapped = raw
            stage = unwrapped.get("development_stage")
            if not isinstance(stage, dict):
                raise GenerationError(
                    f"stage {stage_plan.stage_id} response is missing development_stage",
                    stats=item_stats,
                )
            return stage_plan.sequence, stage, unwrapped, item_stats

        stage_results: dict[int, tuple[dict[str, Any], dict[str, Any], ModelCallStats]] = {}
        failures: list[tuple[DevelopmentStageResearchPlan, GenerationError]] = []
        with ThreadPoolExecutor(
            max_workers=min(
                self.config.generation_development_workers,
                len(plan.development_stage_plans),
            ),
            thread_name_prefix="onboarding-development-stage",
        ) as executor:
            futures = {
                executor.submit(generate_stage, stage_plan): stage_plan
                for stage_plan in plan.development_stage_plans
            }
            for future in as_completed(futures):
                try:
                    sequence, stage, unwrapped, item_stats = future.result()
                    stage_results[sequence] = (stage, unwrapped, item_stats)
                except GenerationError as error:
                    stage_plan = futures[future]
                    failures.append(
                        (
                            stage_plan,
                            GenerationError(
                                f"stage {stage_plan.stage_id} failed: {error}",
                                stats=error.stats,
                            ),
                        )
                    )
        total_stats = ModelCallStats()
        self._add_stats(total_stats, foundation_stats)
        for _, _, item_stats in stage_results.values():
            self._add_stats(total_stats, item_stats)
        for stage_plan, error in failures:
            self._add_stats(total_stats, error.stats)
            stage_results[stage_plan.sequence] = (
                self._fallback_development_stage(
                    stage_plan,
                    paper_by_id,
                    error=str(error),
                ),
                {"paper_guidance": [], "evidence_claims": []},
                error.stats,
            )
            total_stats.degraded_sections.append(
                f"development_stage:{stage_plan.stage_id}"
            )
            total_stats.failure_reasons.append(str(error))
        if len(stage_results) != len(plan.development_stage_plans):
            missing = [
                stage.stage_id
                for stage in plan.development_stage_plans
                if stage.sequence not in stage_results
            ]
            raise GenerationError(
                f"researched development stages are incomplete: {missing}",
                stats=total_stats,
            )

        combined = {
            "domain": foundation.get("domain") or plan.normalized_domain,
            "text": foundation.get("text") or "",
            "prerequisites": foundation["prerequisites"],
            "development_stages": [
                stage_results[sequence][0] for sequence in sorted(stage_results)
            ],
            # Foundation calls deliberately do not own paper guidance or
            # evidence; those bindings come from the independently researched
            # historical stages below.
            "paper_guidance": [],
            "evidence_claims": [],
        }
        for sequence in sorted(stage_results):
            _, unwrapped, _ = stage_results[sequence]
            combined["paper_guidance"].extend(
                self._mapping_items(unwrapped.get("paper_guidance"))
            )
            combined["evidence_claims"].extend(
                self._mapping_items(unwrapped.get("evidence_claims"))
            )
        completed = self._complete_section_payload(
            "development",
            combined,
            papers,
            default_domain=plan.normalized_domain,
            plan=plan,
        )
        return completed, total_stats

    @staticmethod
    def _fallback_development_stage(
        stage_plan: DevelopmentStageResearchPlan,
        paper_by_id: dict[str, RankedPaper],
        *,
        error: str,
    ) -> dict[str, Any]:
        """Preserve planner facts without inventing prose after a stage failure."""

        paper_ids = [
            paper_id
            for paper_id in stage_plan.selected_paper_ids
            if paper_id in paper_by_id
        ]
        return {
            "stage_id": stage_plan.stage_id,
            "sequence": stage_plan.sequence,
            "name": stage_plan.name,
            "period": stage_plan.period,
            "historical_period": stage_plan.period,
            "summary": stage_plan.focus,
            "motivation": stage_plan.focus,
            "transition_from_previous": stage_plan.transition_from_previous,
            "related_paper_ids": paper_ids,
            "core_concepts": [],
            "main_techniques": [],
            "breakthroughs": [],
            "open_problems": [],
            "generation_status": "degraded",
            "generation_error": error,
        }

    def _invoke_development_piece(
        self,
        model: Any,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        timeout_seconds: float,
        stream_stage: str,
        on_delta: Callable[[str, str], None] | None,
        contract: ResponseContract,
    ) -> tuple[dict[str, Any], ModelCallStats]:
        retry_instruction = ""

        def operation(candidate: Any, attempt_timeout: float | None):
            return invoke_json(
                candidate,
                system_prompt=system_prompt + retry_instruction,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                timeout_seconds=attempt_timeout,
                on_delta=on_delta,
                stream_stage=stream_stage,
                contract=contract,
            )

        total_stats = ModelCallStats()
        last_error: StructuredLLMError | None = None
        for attempt in range(self.config.generation_max_attempts):
            retry_instruction = self._json_retry_instruction(attempt)
            try:
                payload, attempt_stats = run_with_model_route(
                    model,
                    operation,
                    timeout_seconds=timeout_seconds,
                )
            except StructuredLLMError as error:
                last_error = error
                self._add_stats(total_stats, error.stats)
                self._wait_before_retry(attempt)
                continue
            self._add_stats(total_stats, attempt_stats)
            return payload, total_stats
        assert last_error is not None
        raise GenerationError(str(last_error), stats=total_stats) from last_error

    @staticmethod
    def _json_retry_instruction(attempt: int) -> str:
        if attempt <= 0:
            return ""
        return (
            "\n\nThe previous response could not be parsed or validated. "
            "Return exactly one complete JSON object matching the requested schema. "
            "Do not include analysis, markdown fences, comments, or text before or "
            "after the JSON object."
        )

    def _wait_before_retry(self, attempt: int) -> None:
        if attempt + 1 >= self.config.generation_max_attempts:
            return
        delay = min(
            60.0,
            self.config.generation_retry_backoff_seconds * (attempt + 1),
        )
        if delay <= 0:
            return
        cancel_event = current_cancel_event()
        if cancel_event is not None:
            if cancel_event.wait(delay):
                raise GenerationError("LLM call cancelled")
            return
        from time import sleep

        sleep(delay)

    @staticmethod
    def _add_stats(total: ModelCallStats, item: ModelCallStats) -> None:
        total.duration_ms += item.duration_ms
        total.model_calls += item.model_calls
        total.prompt_tokens += item.prompt_tokens
        total.completion_tokens += item.completion_tokens
        total.total_tokens += item.total_tokens
        total.usage_reported = total.usage_reported or item.usage_reported
        total.degraded_sections = list(
            dict.fromkeys([*total.degraded_sections, *item.degraded_sections])
        )
        total.failure_reasons = list(
            dict.fromkeys([*total.failure_reasons, *item.failure_reasons])
        )

    def _call_section(
        self,
        section: str,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        completed: dict[str, Any],
        on_delta: Callable[[str, str], None] | None = None,
    ) -> tuple[dict[str, Any], ModelCallStats]:
        examples = {
            "development": (
                '{"domain":"domain","text":"summary","prerequisites":[{"name":"foundation",'
                '"why_needed":"reason","key_points":[{"name":"concept","explanation":"plain explanation",'
                '"why_it_matters":"learning value","related_paper_ids":["paper_1"]}],"related_paper_ids":["paper_1"]}],'
                '"development_stages":[{"stage_id":"stage_1","name":"stage","historical_period":"2020-2022",'
                '"summary":"summary","related_paper_ids":["paper_1"],"core_concepts":[{"name":"concept",'
                '"explanation":"plain explanation","why_it_matters":"learning value","related_paper_ids":["paper_1"]}],'
                '"main_techniques":[{"name":"technique","explanation":"what it does","mechanism":"how it works",'
                '"why_it_matters":"problem solved","related_paper_ids":["paper_1"]}],"breakthroughs":['
                '{"breakthrough_id":"breakthrough_1","name":"advance","description":"what changed",'
                '"supporting_paper_ids":["paper_1"],"enabled_capabilities":["capability"],'
                '"limitation_problem_ids":[]}]}],"paper_guidance":[],"evidence_claims":[]}'
            ),
            "landscape": (
                '{"current_landscape":{"problems":["problem"],"subdirections":["direction"],'
                '"problem_details":[{"problem_id":"problem_1","name":"problem",'
                '"description":"current research challenge","related_paper_ids":["paper_1"],'
                '"related_stage_ids":["stage_1"]}],"subdirection_details":['
                '{"subdirection_id":"sub_1","name":"direction",'
                '"description":"scope","why_it_matters":"importance","typical_tasks":["task"],'
                '"prerequisites":["foundation"],"common_techniques":[{"name":"method",'
                '"explanation":"what it does","mechanism":"how it works","why_it_matters":"value",'
                '"related_paper_ids":["paper_1"]}],"datasets_and_benchmarks":["dataset"],'
                '"evaluation_metrics":["metric"],"starter_project":"reproduce a baseline",'
                '"research_workflow":["reproduce","analyze","improve","evaluate"],'
                '"research_questions":["question"],"related_paper_ids":["paper_1"],'
                '"related_stage_ids":["stage_1"],"emerged_in_stage_id":"stage_1",'
                '"addresses_problem_ids":["problem_1"]}]},"evidence_claims":[]}'
            ),
            "learning_path": (
                '{"learning_path":[{"step":"1","goal":"goal","topics":["topic"],'
                '"paper_ids":[],"activities":["activity"],"completion_criteria":["criterion"],'
                '"expected_outcome":"outcome"}],"evidence_claims":[]}'
            ),
        }
        system_prompt = (
            section_system_prompt(section, request.language)
            + " Use these exact top-level keys. Example JSON shape: "
            + examples[section]
        )
        user_payload = self._section_user_payload(
            section,
            request,
            profile,
            plan,
            papers,
            completed,
        )
        section_model = self.section_models.get(section, self.model)
        retry_instruction = ""

        def generate_candidate(candidate: Any, timeout_seconds: float | None):
            payload, stats = invoke_json(
                candidate,
                system_prompt=system_prompt + retry_instruction,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                timeout_seconds=timeout_seconds,
                on_delta=on_delta,
                stream_stage=section,
                contract=SECTION_CONTRACTS[section],
            )
            try:
                completed = self._complete_section_payload(
                    section,
                    payload,
                    papers,
                    default_domain=plan.normalized_domain,
                    plan=plan,
                )
            except GenerationError as error:
                error.stats = stats
                raise
            return completed, stats

        section_timeout = {
            "development": self.config.generation_development_timeout_seconds,
            "landscape": self.config.generation_landscape_timeout_seconds,
            "learning_path": self.config.generation_learning_path_timeout_seconds,
        }[section]
        total_stats = ModelCallStats()
        last_error: StructuredLLMError | GenerationError | None = None
        for attempt in range(self.config.generation_max_attempts):
            retry_instruction = self._json_retry_instruction(attempt)
            try:
                completed, attempt_stats = run_with_model_route(
                    section_model,
                    generate_candidate,
                    timeout_seconds=section_timeout,
                )
            except (StructuredLLMError, GenerationError) as error:
                last_error = error
                error.stats.model_calls = max(
                    error.stats.model_calls,
                    int(getattr(section_model, "last_attempt_count", 1)),
                )
                self._add_stats(total_stats, error.stats)
                self._wait_before_retry(attempt)
                continue
            attempt_stats.model_calls = max(
                attempt_stats.model_calls,
                int(getattr(section_model, "last_attempt_count", 1)),
            )
            self._add_stats(total_stats, attempt_stats)
            return completed, total_stats
        assert last_error is not None
        raise GenerationError(str(last_error), stats=total_stats) from last_error

    def _complete_section_payload(
        self,
        section: str,
        payload: dict[str, Any],
        papers: list[RankedPaper],
        *,
        default_domain: str = "",
        plan: DomainResearchPlan | None = None,
    ) -> dict[str, Any]:
        try:
            payload = adapt_structured_response(
                payload, SECTION_CONTRACTS[section]
            ).data
        except StructuredResponseError as error:
            raise GenerationError(str(error)) from error
        completed = dict(payload)
        # The normalized domain is planner-owned data. Copying it here is safer
        # than spending another model attempt when a structurally valid
        # development response merely omits that redundant field.
        if section == "development" and not str(completed.get("domain") or "").strip():
            completed["domain"] = default_domain
        if section == "development" and not isinstance(
            completed.get("prerequisites"), list
        ):
            raise GenerationError("development section is missing prerequisites")
        if section == "development" and not isinstance(
            completed.get("development_stages"), list
        ):
            raise GenerationError("development section is missing development stages")
        if section == "development":
            self._sanitize_development_paper_ids(completed, papers, plan=plan)
        if section == "landscape" and not isinstance(
            completed.get("current_landscape"), dict
        ):
            raise GenerationError("landscape section is missing current_landscape")
        if section == "learning_path" and not isinstance(
            completed.get("learning_path"), list
        ):
            raise GenerationError("learning path section is missing learning_path")
        self._validate_section_payload(section, completed, papers)
        return completed

    def _sanitize_development_paper_ids(
        self,
        payload: dict[str, Any],
        papers: list[RankedPaper],
        *,
        plan: DomainResearchPlan | None = None,
    ) -> None:
        """Bind generated development claims to canonical retrieved paper IDs."""
        allowed_ids = {paper.paper_id for paper in papers}
        ranked_ids = [paper.paper_id for paper in papers]
        stages = payload.get("development_stages")
        if not isinstance(stages, list) or not ranked_ids:
            return
        stage_plans = plan.development_stage_plans if plan is not None else []
        if stage_plans and len(stages) != len(stage_plans):
            raise GenerationError(
                "development stages do not match the researched stage outline"
            )
        for index, stage in enumerate(stages):
            if not isinstance(stage, dict):
                continue
            planned = stage_plans[index] if index < len(stage_plans) else None
            if planned is not None:
                stage.update(
                    {
                        "stage_id": planned.stage_id,
                        "sequence": planned.sequence,
                        "name": planned.name,
                        "historical_period": planned.period,
                        "period": planned.period,
                    }
                )
                if not str(stage.get("transition_from_previous") or "").strip():
                    stage["transition_from_previous"] = planned.transition_from_previous
                stage_allowed_ids = set(planned.selected_paper_ids) & allowed_ids
            else:
                stage_allowed_ids = allowed_ids
            stage_ids = [
                paper_id
                for paper_id in self._strings(stage.get("related_paper_ids"))
                if paper_id in stage_allowed_ids
            ]
            if not stage_ids:
                stage_ids = [
                    paper_id
                    for paper_id in (
                        planned.selected_paper_ids if planned is not None else ranked_ids
                    )
                    if paper_id in allowed_ids
                ]
            if not stage_ids:
                raise GenerationError(
                    f"development stage {index} has no researched paper evidence"
                )
            stage["related_paper_ids"] = list(dict.fromkeys(stage_ids))
            breakthroughs = stage.get("breakthroughs")
            if not isinstance(breakthroughs, list):
                singular = (
                    stage.get("breakthrough")
                    or stage.get("key_breakthrough")
                    or stage.get("major_breakthrough")
                )
                if isinstance(singular, dict):
                    breakthroughs = [singular]
                    stage["breakthroughs"] = breakthroughs
            for collection_name in ("core_concepts", "main_techniques"):
                collection = stage.get(collection_name)
                if not isinstance(collection, list):
                    continue
                for item in collection:
                    if not isinstance(item, dict):
                        continue
                    item_ids = [
                        paper_id
                        for paper_id in self._strings(item.get("related_paper_ids"))
                        if paper_id in stage_ids
                    ]
                    item["related_paper_ids"] = item_ids or list(stage_ids)
            if not isinstance(breakthroughs, list):
                continue
            for breakthrough in breakthroughs:
                if not isinstance(breakthrough, dict):
                    continue
                supporting_ids = [
                    paper_id
                    for paper_id in self._strings(
                        breakthrough.get("supporting_paper_ids")
                        or breakthrough.get("paper_ids")
                        or breakthrough.get("related_paper_ids")
                    )
                    if paper_id in allowed_ids
                ]
                breakthrough["supporting_paper_ids"] = list(
                    dict.fromkeys(supporting_ids or stage_ids)
                )

    def _section_user_payload(
        self,
        section: str,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Send each section only the context it can actually consume."""
        del profile
        plan_payload = plan.model_dump(
            mode="json",
            include={
                "normalized_domain",
                "translated_domain",
                "expanded_terms",
                "perspectives",
                "expected_subdirections",
                "subdirection_plans",
                "development_stage_plans",
            },
        )
        completed_keys = {
            "development": set(),
            "landscape": {"domain", "development_stages"},
            "learning_path": {"domain", "prerequisites", "development_stages"},
        }[section]
        return {
            "request": {
                "domain": plan.normalized_domain,
                "language": request.language,
            },
            "research_plan": plan_payload,
            "allowed_papers": [self._paper_prompt_payload(paper) for paper in papers],
            "completed_sections": {
                key: value for key, value in completed.items() if key in completed_keys
            },
        }

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
                if (
                    isinstance(stage, dict)
                    and stage.get("generation_status") == "degraded"
                ):
                    continue
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
            problem_details = landscape.get("problem_details")
            subdirection_details = landscape.get("subdirection_details")
            internal_problem_names = [
                str(item.get("name") or "").strip()
                for item in problem_details
                if isinstance(item, dict)
                and is_internal_landscape_label(item.get("name"))
            ] if isinstance(problem_details, list) else []
            internal_subdirection_names = [
                str(item.get("name") or "").strip()
                for item in subdirection_details
                if isinstance(item, dict)
                and is_internal_landscape_label(item.get("name"))
            ] if isinstance(subdirection_details, list) else []
            if internal_problem_names or internal_subdirection_names:
                raise GenerationError(
                    "landscape detail names must be reader-facing labels, not internal IDs"
                )
            valid_problems = [
                item
                for item in problem_details
                if isinstance(item, dict)
                and str(item.get("name") or "").strip()
                and str(item.get("description") or "").strip()
            ] if isinstance(problem_details, list) else []
            valid_subdirections = [
                item
                for item in subdirection_details
                if isinstance(item, dict)
                and str(item.get("name") or "").strip()
                and (
                    str(item.get("description") or "").strip()
                    or str(item.get("why_it_matters") or "").strip()
                )
            ] if isinstance(subdirection_details, list) else []
            if len(valid_problems) < 3:
                raise GenerationError(
                    "landscape section requires 3 non-empty problem_details"
                )
            if len(valid_subdirections) < self.config.min_subdirections:
                raise GenerationError(
                    "landscape section requires 3 non-empty subdirection_details"
                )
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
        on_delta: Callable[[str, str], None] | None = None,
    ) -> GenerationResult:
        del profile
        from .repair_diff import apply_repair_values, target_values

        target_paths = list(
            dict.fromkeys(issue.target_path for issue in issues if issue.target_path)
        )
        current_targets = target_values(previous_output, target_paths)
        system_prompt = (
            "Repair only the supplied JSON target values. Return one JSON object with "
            'the shape {"repairs":[{"target_path":"exact supplied path","value":...}]}. '
            "Do not return the complete onboarding result. Do not change a path that was "
            "not supplied. Preserve valid paper IDs and use only allowed paper IDs. "
            f"Write explanatory text in {request.language}."
        )
        user_payload = {
            "request": {"domain": plan.normalized_domain, "language": request.language},
            "repair_issues": [issue.model_dump(mode="json") for issue in issues],
            "current_targets": current_targets,
            "allowed_papers": [self._paper_prompt_payload(paper) for paper in papers],
        }
        try:
            payload, stats = invoke_json(
                self.repair_model,
                system_prompt=system_prompt,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                timeout_seconds=self.config.repair_timeout_seconds,
                on_delta=on_delta,
                stream_stage="repair",
            )
        except StructuredLLMError as error:
            raise GenerationError(str(error), stats=error.stats) from error
        try:
            try:
                payload = adapt_structured_response(
                    payload, REPAIR_PATCH_CONTRACT
                ).data
            except StructuredResponseError:
                # Preserve compatibility with custom repair models that return
                # a complete onboarding document instead of a targeted patch.
                payload = adapt_structured_response(
                    payload, FULL_ONBOARDING_CONTRACT
                ).data
            repairs = payload.get("repairs")
            if isinstance(repairs, list):
                output = apply_repair_values(
                    previous_output,
                    [item for item in repairs if isinstance(item, dict)],
                    target_paths,
                )
            else:
                # Backward compatibility for custom repair models that still
                # return a complete onboarding object.
                output = self._normalize(
                    payload,
                    request,
                    previous_output.learner_profile,
                    plan,
                    papers,
                )
        except (GenerationError, ValidationError, ValueError, TypeError) as error:
            if isinstance(error, GenerationError):
                error.stats = stats
                raise
            raise GenerationError(
                f"targeted repair patch failed validation: {error}",
                stats=stats,
            ) from error
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
        on_delta: Callable[[str, str], None] | None = None,
    ) -> tuple[dict[str, Any], ModelCallStats]:
        del profile
        system_prompt = full_generation_system_prompt(request.language)
        user_payload: dict[str, Any] = {
            "request": {"domain": plan.normalized_domain, "language": request.language},
            "research_plan": plan.model_dump(mode="json"),
            "allowed_papers": [self._paper_prompt_payload(paper) for paper in papers],
        }
        if previous_output is not None:
            user_payload["previous_output"] = previous_output.model_dump(
                mode="json",
                exclude={"learner_profile", "papers", "evidence_papers"},
            )
            user_payload["repair_issues"] = [issue.model_dump(mode="json") for issue in issues or []]
            user_payload["instruction"] = "Repair only the reported weaknesses while preserving valid paper IDs."
        try:
            payload, stats = invoke_json(
                self.repair_model if previous_output is not None else self.model,
                system_prompt=system_prompt,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                timeout_seconds=(
                    self.config.repair_timeout_seconds
                    if previous_output is not None
                    else self.config.generation_timeout_seconds
                ),
                on_delta=on_delta,
                stream_stage="repair" if previous_output is not None else "generation",
                contract=FULL_ONBOARDING_CONTRACT,
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
            "citation_count": paper.citation_count,
            "citation_status": paper.citation_status,
            "paper_usage": paper.paper_usage,
            "recommendation_category": paper.recommendation_category,
            "recommendation_reason": paper.recommendation_reason,
            "survey_source_ids": paper.survey_source_ids,
        }

    def _normalize(
        self,
        payload: dict[str, Any],
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
    ) -> DomainOnboardingOutput:
        # Direct generator callers may still pass a legacy inferred profile.
        # Normalize it away so every entry point produces the same route.
        profile = standard_novice_profile()
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
                    or paper.recommendation_reason
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
        prerequisites = self._normalize_prerequisites(
            payload.get("prerequisites"),
            references,
            request=request,
            plan=plan,
        )
        stages = self._normalize_stages(payload.get("development_stages"), references, prerequisites, papers)
        stages = self._align_stages_to_research_plan(stages, plan, references)
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
            has_recommendation_contract = any(
                paper.paper_usage in {"recommendation", "both"}
                for paper in selected_papers
            )
            recommended_papers = [
                paper
                for paper in selected_papers
                if paper.paper_usage in {"recommendation", "both"}
            ]
            evidence_papers = [
                paper
                for paper in selected_papers
                if paper.paper_usage in {"evidence", "both"}
            ]
            if (
                not recommended_papers
                and plan.recommendation_strategy
                not in {"survey_degraded_no_result", "survey_success"}
            ):
                recommended_papers = list(selected_papers)
            if not evidence_papers:
                evidence_papers = list(selected_papers)
            # The backend owns the public order. Clients may sort defensively,
            # but every emitted list follows the same final_score contract.
            recommended_papers.sort(key=lambda paper: paper.final_score, reverse=True)
            evidence_papers.sort(key=lambda paper: paper.final_score, reverse=True)
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
                papers=recommended_papers,
                evidence_papers=evidence_papers,
                evidence_claims=evidence_claims,
                reproducibility={
                    "policy_version": self.config.policy_version,
                    "search_queries": plan.search_queries,
                    "retrieval_sources": sorted({paper.source for paper in papers}),
                    "selected_paper_ids": [paper.paper_id for paper in papers],
                    "evidence_paper_ids": [paper.paper_id for paper in evidence_papers],
                    "recommended_paper_ids": [
                        paper.paper_id for paper in recommended_papers
                    ],
                    "recommendation_strategy": (
                        plan.recommendation_strategy
                        if plan.recommendation_strategy != "not_run"
                        else "survey_success"
                        if has_recommendation_contract
                        else "legacy_selected_papers"
                    ),
                    "generation_model_routes": self.model_routing_snapshot(),
                },
            )
        except ValidationError as error:
            raise GenerationError(f"generated output failed validation: {error}") from error

    @staticmethod
    def _align_stages_to_research_plan(
        stages: list[DevelopmentStage],
        plan: DomainResearchPlan,
        references: dict[str, PaperReference],
    ) -> list[DevelopmentStage]:
        researched = plan.development_stage_plans
        if not researched:
            return stages
        if len(stages) != len(researched):
            raise GenerationError(
                "generated development stages do not match the researched stage outline"
            )
        for index, (stage, stage_plan) in enumerate(zip(stages, researched, strict=True)):
            allowed_ids = {
                paper_id
                for paper_id in stage_plan.selected_paper_ids
                if paper_id in references
            }
            if not allowed_ids:
                raise GenerationError(
                    f"researched development stage {index} has no selected papers"
                )
            stage.stage_id = stage_plan.stage_id
            stage.sequence = stage_plan.sequence
            stage.name = stage_plan.name
            stage.period = stage_plan.period
            stage.historical_period = stage_plan.period
            stage.previous_stage_id = (
                None if index == 0 else researched[index - 1].stage_id
            )
            if not stage.transition_from_previous:
                stage.transition_from_previous = stage_plan.transition_from_previous
            stage.related_paper_ids = [
                paper_id for paper_id in stage.related_paper_ids if paper_id in allowed_ids
            ] or list(stage_plan.selected_paper_ids)
            stage.representative_papers = [
                references[paper_id]
                for paper_id in stage.related_paper_ids
                if paper_id in references
            ]
            for detail in [*stage.core_concepts, *stage.main_techniques]:
                detail.related_paper_ids = [
                    paper_id
                    for paper_id in detail.related_paper_ids
                    if paper_id in allowed_ids
                ] or list(stage.related_paper_ids)
            for breakthrough in stage.breakthroughs:
                breakthrough.supporting_paper_ids = [
                    paper_id
                    for paper_id in breakthrough.supporting_paper_ids
                    if paper_id in allowed_ids
                ] or list(stage.related_paper_ids)
        return stages

    def model_routing_snapshot(self) -> dict[str, Any]:
        models = {
            "generation": self.model,
            **self.section_models,
            "repair": self.repair_model,
        }
        return {
            name: snapshot
            for name, model in models.items()
            if (snapshot := routing_snapshot(model)) is not None
        }

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
            paper_ids = self._valid_ids(
                item.get("supporting_paper_ids")
                or item.get("paper_ids")
                or item.get("evidence_paper_ids")
                or item.get("citations"),
                references,
            )
            evidence = item.get("evidence")
            if isinstance(evidence, list):
                paper_ids = list(
                    dict.fromkeys(
                        [*paper_ids, *self._valid_ids(evidence, references)]
                    )
                )
            elif isinstance(evidence, str):
                # Some JSON models provide a grounded prose sentence instead
                # of a separate ID array. Accept only IDs that occur verbatim
                # in that sentence and are already in the selected paper set.
                paper_ids = list(
                    dict.fromkeys(
                        [
                            *paper_ids,
                            *(
                                paper_id
                                for paper_id in references
                                if paper_id in evidence
                            ),
                        ]
                    )
                )
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
        *,
        request: DomainOnboardingRequest,
        plan: DomainResearchPlan,
    ) -> list[Prerequisite]:
        items = value if isinstance(value, list) else []
        results: list[Prerequisite] = []
        for item in items:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            ids = self._valid_ids(item.get("related_paper_ids"), references)
            key_points = self._concept_details(
                item.get("key_points"), references, fallback_ids=ids
            )
            ids = list(
                dict.fromkeys(
                    [*ids, *(paper_id for detail in key_points for paper_id in detail.related_paper_ids)]
                )
            )
            results.append(
                Prerequisite(
                    prerequisite_id=item.get("prerequisite_id"),
                    name=str(item["name"]),
                    why_needed=str(item.get("why_needed") or ""),
                    key_points=key_points,
                    related_paper_ids=ids,
                )
            )
        if results:
            return results
        return self._fallback_prerequisites(request, plan, references)

    @staticmethod
    def _fallback_prerequisites(
        request: DomainOnboardingRequest,
        plan: DomainResearchPlan,
        references: dict[str, PaperReference],
    ) -> list[Prerequisite]:
        """Provide a minimal beginner foundation only when model output is empty."""

        english = request.language == "en-US"
        domain = plan.normalized_domain or request.query
        paper_ids = list(references)[:3]

        def evidence_for(index: int) -> list[str]:
            if not paper_ids:
                return []
            return [paper_ids[index % len(paper_ids)]]

        perspective_points = [
            ConceptDetail(
                name=item.name,
                explanation=item.description,
                why_it_matters=(
                    "It frames the main research question in this field."
                    if english
                    else "它帮助理解该领域主要研究问题的边界。"
                ),
                related_paper_ids=evidence_for(index),
            )
            for index, item in enumerate(plan.perspectives[:2])
        ]
        if not perspective_points:
            perspective_points = [
                ConceptDetail(
                    name=domain,
                    explanation=(
                        f"Core terminology and problem definitions for {domain}."
                        if english
                        else f"{domain}的核心术语、研究对象与问题定义。"
                    ),
                    related_paper_ids=evidence_for(0),
                )
            ]

        branch_names = [
            item.name_en if english else item.name_zh
            for item in plan.subdirection_plans[:2]
        ] or list(plan.expected_subdirections[:2])
        method_points = [
            ConceptDetail(
                name=name,
                explanation=(
                    f"Understand the core method family represented by {name}."
                    if english
                    else f"理解“{name}”所代表的核心方法与技术路线。"
                ),
                related_paper_ids=evidence_for(index + 1),
            )
            for index, name in enumerate(branch_names)
            if name
        ]
        if not method_points:
            method_points = [
                ConceptDetail(
                    name="Methods and technical routes" if english else "方法与技术路线",
                    explanation=(
                        "Compare assumptions, inputs, outputs, and applicable conditions."
                        if english
                        else "比较不同方法的假设、输入输出与适用条件。"
                    ),
                    related_paper_ids=evidence_for(1),
                )
            ]

        validation_points = [
            ConceptDetail(
                name="Claim-method-evidence chain" if english else "问题—方法—证据链",
                explanation=(
                    "Connect each research claim to its method and experimental evidence."
                    if english
                    else "把论文中的研究主张对应到方法设计和实验依据。"
                ),
                related_paper_ids=evidence_for(2),
            ),
            ConceptDetail(
                name="Evaluation and reproducibility" if english else "评测与复现",
                explanation=(
                    "Read datasets, metrics, baselines, and reproducibility conditions together."
                    if english
                    else "结合数据集、指标、基线与复现条件判断结论是否可靠。"
                ),
                related_paper_ids=evidence_for(0),
            ),
        ]
        return [
            Prerequisite(
                name=f"{domain} core concepts" if english else f"{domain}核心概念",
                why_needed=(
                    "Build a shared vocabulary before reading representative papers."
                    if english
                    else "先建立共同术语和问题边界，避免读论文时只记结论。"
                ),
                key_points=perspective_points,
                related_paper_ids=list(dict.fromkeys(
                    paper_id
                    for point in perspective_points
                    for paper_id in point.related_paper_ids
                )),
            ),
            Prerequisite(
                name="Methods and technical foundations" if english else "方法与技术基础",
                why_needed=(
                    "Recognize the assumptions and trade-offs behind different approaches."
                    if english
                    else "理解不同技术路线的基本假设、适用条件与取舍。"
                ),
                key_points=method_points,
                related_paper_ids=list(dict.fromkeys(
                    paper_id
                    for point in method_points
                    for paper_id in point.related_paper_ids
                )),
            ),
            Prerequisite(
                name="Paper reading and validation" if english else "论文阅读与实验验证",
                why_needed=(
                    "Judge whether a paper's evidence supports its main conclusions."
                    if english
                    else "学会判断论文的实验依据是否真正支撑主要结论。"
                ),
                key_points=validation_points,
                related_paper_ids=list(dict.fromkeys(
                    paper_id
                    for point in validation_points
                    for paper_id in point.related_paper_ids
                )),
            ),
        ]

    def _normalize_landscape(
        self,
        value: dict[str, Any],
        plan: DomainResearchPlan,
        stages: list[DevelopmentStage],
        references: dict[str, PaperReference],
    ) -> CurrentLandscape:
        problem_names = self._strings(value.get("problems"))
        researched_subdirections = any(
            branch.selected_paper_ids for branch in plan.subdirection_plans
        )
        subdirection_names = (
            [branch.name_zh for branch in plan.subdirection_plans]
            if researched_subdirections
            else self._strings(value.get("subdirections"))
        ) or list(plan.expected_subdirections)
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
        raw_subdirection_details = value.get("subdirection_details")
        legacy_subdirection_aliases: dict[str, str] = {}
        if researched_subdirections and isinstance(raw_subdirection_details, list):
            normalized_raw_details: list[object] = []
            for index, item in enumerate(raw_subdirection_details):
                if not isinstance(item, dict) or index >= len(plan.subdirection_plans):
                    normalized_raw_details.append(item)
                    continue
                branch = plan.subdirection_plans[index]
                raw = dict(item)
                for alias in (
                    str(raw.get("subdirection_id") or ""),
                    str(raw.get("name") or ""),
                ):
                    if alias:
                        legacy_subdirection_aliases[alias] = branch.subdirection_id
                raw["subdirection_id"] = branch.subdirection_id
                raw["name"] = branch.name_zh
                normalized_raw_details.append(raw)
            raw_subdirection_details = normalized_raw_details
        subdirection_details = self._subdirection_details(
            raw_subdirection_details,
            subdirection_names,
            references,
            stage_aliases,
        )
        for index, detail in enumerate(subdirection_details):
            if not researched_subdirections:
                break
            if index >= len(plan.subdirection_plans):
                break
            branch = plan.subdirection_plans[index]
            allowed_ids = set(branch.selected_paper_ids) & set(references)
            detail.subdirection_id = branch.subdirection_id
            detail.name = branch.name_zh
            detail.related_paper_ids = [
                paper_id
                for paper_id in detail.related_paper_ids
                if paper_id in allowed_ids
            ] or [
                paper_id
                for paper_id in branch.selected_paper_ids
                if paper_id in allowed_ids
            ]
            for technique in detail.common_techniques:
                technique.related_paper_ids = [
                    paper_id
                    for paper_id in technique.related_paper_ids
                    if paper_id in allowed_ids
                ] or list(detail.related_paper_ids)
        subdirection_aliases = {
            alias: str(item.subdirection_id)
            for item in subdirection_details
            if item.subdirection_id
            for alias in (str(item.subdirection_id), item.name)
        }
        subdirection_aliases.update(legacy_subdirection_aliases)
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
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
            and not is_internal_landscape_label(item.get("name"))
        }
        fallback_names = [
            name for name in names if not is_internal_landscape_label(name)
        ]
        ordered_names = list(dict.fromkeys(fallback_names)) or list(by_name)
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
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
            and not is_internal_landscape_label(item.get("name"))
        }
        fallback_names = [
            name for name in names if not is_internal_landscape_label(name)
        ]
        ordered_names = list(dict.fromkeys(fallback_names)) or list(by_name)
        results = []
        for name in ordered_names:
            raw = by_name.get(name, {})
            paper_ids = self._valid_ids(raw.get("related_paper_ids"), references)
            common_techniques = self._technique_details(
                raw.get("common_techniques", raw.get("common_methods")),
                references,
                fallback_ids=paper_ids,
            )
            paper_ids = list(
                dict.fromkeys(
                    [
                        *paper_ids,
                        *(
                            paper_id
                            for technique in common_techniques
                            for paper_id in technique.related_paper_ids
                        ),
                    ]
                )
            )
            results.append(
                SubdirectionDetail(
                    subdirection_id=raw.get("subdirection_id"),
                    name=name,
                    description=str(raw.get("description") or ""),
                    why_it_matters=str(raw.get("why_it_matters") or ""),
                    typical_tasks=self._strings(raw.get("typical_tasks")),
                    prerequisites=self._strings(raw.get("prerequisites")),
                    common_techniques=common_techniques,
                    datasets_and_benchmarks=self._strings(
                        raw.get("datasets_and_benchmarks", raw.get("datasets"))
                    ),
                    evaluation_metrics=self._strings(raw.get("evaluation_metrics")),
                    starter_project=str(
                        raw.get("starter_project") or raw.get("first_project") or ""
                    ),
                    research_workflow=self._strings(raw.get("research_workflow")),
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
            generation_status = (
                "degraded"
                if item.get("generation_status") == "degraded"
                else "generated"
            )
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
            if not breakthroughs and generation_status != "degraded":
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
            core_concepts = self._concept_details(
                item.get("core_concepts"), references, fallback_ids=ids
            )
            main_techniques = self._technique_details(
                item.get("main_techniques"), references, fallback_ids=ids
            )
            ids = list(
                dict.fromkeys(
                    [
                        *ids,
                        *(
                            paper_id
                            for detail in [*core_concepts, *main_techniques]
                            for paper_id in detail.related_paper_ids
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
                    core_concepts=core_concepts,
                    main_techniques=main_techniques,
                    open_problems=self._strings(item.get("open_problems")),
                    breakthroughs=breakthroughs,
                    related_paper_ids=ids,
                    prerequisite_ids=stage_prereqs,
                    generation_status=generation_status,
                    generation_error=(
                        str(item.get("generation_error") or "")
                        if generation_status == "degraded"
                        else ""
                    ),
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

    @staticmethod
    def _mapping_items(value: object) -> list[dict[str, Any]]:
        """Keep only object items from model-owned collection fields."""

        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

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

    @classmethod
    def _concept_details(
        cls,
        value: object,
        references: dict[str, PaperReference],
        *,
        fallback_ids: list[str],
    ) -> list[ConceptDetail]:
        return [
            ConceptDetail.model_validate(item)
            for item in cls._detail_payloads(value, references, fallback_ids=fallback_ids)
        ]

    @classmethod
    def _technique_details(
        cls,
        value: object,
        references: dict[str, PaperReference],
        *,
        fallback_ids: list[str],
    ) -> list[TechniqueDetail]:
        return [
            TechniqueDetail.model_validate(item)
            for item in cls._detail_payloads(
                value, references, fallback_ids=fallback_ids, technique=True
            )
        ]

    @classmethod
    def _detail_payloads(
        cls,
        value: object,
        references: dict[str, PaperReference],
        *,
        fallback_ids: list[str],
        technique: bool = False,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in cls._as_list(value):
            item = {"name": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else {}
            name = str(item.get("name") or item.get("title") or item.get("label") or "").strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            paper_ids = cls._valid_ids(
                item.get("related_paper_ids", item.get("paper_ids")), references
            ) or list(fallback_ids)
            payload: dict[str, object] = {
                "name": name,
                "explanation": str(item.get("explanation") or item.get("description") or "").strip(),
                "why_it_matters": str(item.get("why_it_matters") or "").strip(),
                "related_paper_ids": paper_ids,
            }
            id_key = "technique_id" if technique else "concept_id"
            if item.get(id_key):
                payload[id_key] = item[id_key]
            if technique:
                payload["mechanism"] = str(item.get("mechanism") or "").strip()
            results.append(payload)
        return results
