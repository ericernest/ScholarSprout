"""仅基于已验证候选论文生成结构化领域入门内容。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    LandscapeProblem,
    LearningStep,
    ModelCallStats,
    PaperReference,
    Prerequisite,
    QualityIssue,
    RankedPaper,
    SelectedPaper,
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
        for index, stage_name in enumerate(self.stage_names, start=1):
            raw = provided[index - 1] if index - 1 < len(provided) and isinstance(provided[index - 1], dict) else {}
            paper_optional = not raw and (
                (index == 1 and len(papers) < len(self.stage_names))
                or (index == 5 and len(papers) < len(self.stage_names) - 1)
            )
            ids = self._valid_ids(raw.get("paper_ids"), references)
            desired_roles = {
                1: {"survey", "foundational"},
                2: {"survey", "foundational", "method"},
                3: {"foundational", "method"},
                4: {"method", "evaluation", "application"},
                5: {"frontier", "application", "evaluation", "method", "survey"},
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
            if index == 1 and not paper_optional:
                core_candidates = [
                    paper
                    for paper in papers
                    if paper.reading_priority == "core"
                    and paper.paper_role in desired_roles
                ]
                if core_candidates and not any(
                    paper_by_id[paper_id].reading_priority == "core"
                    for paper_id in ids
                ):
                    ids = [core_candidates[0].paper_id]
            if not ids and papers and not paper_optional:
                eligible = [
                    paper
                    for paper in papers
                    if paper.paper_role in desired_roles
                ]
                used_ids = {
                    paper_id for step in results for paper_id in step.paper_ids
                }
                if not raw:
                    unused_eligible = [
                        paper for paper in eligible if paper.paper_id not in used_ids
                    ]
                    unused_any = [
                        paper for paper in papers if paper.paper_id not in used_ids
                    ]
                    eligible = unused_eligible or unused_any or eligible
                preferred_roles = {
                    1: ("foundational", "survey"),
                    2: ("foundational", "survey", "method"),
                    3: ("method", "foundational"),
                    4: ("evaluation", "method", "application"),
                    5: ("frontier", "application", "method", "evaluation", "survey"),
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
            default_activities = {
                1: ["建立术语表并完成前置知识自测"],
                2: ["绘制核心系统架构图并标注关键数据流"],
                3: ["拆解代表方法的模块、假设与失效条件"],
                4: ["复现一个公开基线并固化数据、依赖与配置"],
                5: ["整理失败案例并形成一项可验证的改进提案"],
            }
            default_deliverables = {
                1: ["前置知识检查清单与核心术语表"],
                2: ["核心架构图与概念对照表"],
                3: ["代表方法模块拆解与复现假设清单"],
                4: ["可运行基线、锁定依赖、实验日志与指标报告"],
                5: ["失败案例分析与下一步改进提案"],
            }
            activities = self._strings(raw.get("activities")) or list(
                default_activities[index]
            )
            if profile.preference == "experiment_first" and index == 4:
                activities.append(
                    "进行至少一组对照实验并解释指标差异"
                    if any("复现" in activity for activity in activities)
                    else "复现一个公开基线，记录配置并完成对照实验"
                )
            elif profile.preference == "theory_first" and index <= 3:
                activities.append("整理概念定义、关键假设与方法推导笔记")
            criteria = self._strings(raw.get("completion_criteria")) or [
                "能够用自己的语言解释本阶段核心内容",
                "完成至少一项可检查的阅读或实验产出",
            ]
            goal = str(raw.get("goal") or f"完成{stage_name}并建立与下一阶段的连接")
            topics = self._strings(raw.get("topics")) or [stage_name]
            outcome = str(raw.get("expected_outcome") or criteria[0])
            deliverables = self._strings(raw.get("deliverables")) or list(
                default_deliverables[index]
            )
            reproducibility = self._strings(raw.get("reproducibility_checklist"))
            evaluation_metrics = self._strings(raw.get("evaluation_metrics"))
            if index == 4:
                reproducibility = reproducibility or [
                    "固定数据集版本、随机种子和依赖版本",
                    "保存基线配置、原始结果和运行日志",
                ]
                evaluation_metrics = evaluation_metrics or ["任务主指标", "延迟与成本"]
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
                    milestone=str(raw.get("milestone") or criteria[0]),
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
            return [item.strip() for item in value if isinstance(item, str) and item.strip()]
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
        fallback_sections: list[str] = []
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

        try:
            development_payload, development_stats = self._call_section(
                "development", request, profile, plan, papers, payload
            )
            if not self._section_payload_usable(
                "development", development_payload, papers
            ):
                fallback_sections.append("development")
                development_payload = self._fallback_section_payload(
                    "development", request, profile, plan, papers, payload
                )
        except GenerationError as error:
            fallback_sections.append("development")
            development_payload = self._fallback_section_payload(
                "development", request, profile, plan, papers, payload
            )
            development_stats = error.stats
        apply_section("development", development_payload, development_stats)
        completed_snapshot = {
            "domain": payload.get("domain"),
            "development_stages": [
                {
                    key: stage.get(key)
                    for key in (
                        "stage_id",
                        "sequence",
                        "name",
                        "historical_period",
                        "start_year",
                        "end_year",
                        "related_paper_ids",
                    )
                }
                for stage in payload.get("development_stages", [])
                if isinstance(stage, dict)
            ],
        }
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
            for future in as_completed(futures):
                section = futures[future]
                try:
                    section_payload, section_stats = future.result()
                    if not self._section_payload_usable(
                        section, section_payload, papers
                    ):
                        fallback_sections.append(section)
                        section_payload = self._fallback_section_payload(
                            section,
                            request,
                            profile,
                            plan,
                            papers,
                            completed_snapshot,
                        )
                    completed_sections[section] = (section_payload, section_stats)
                except GenerationError as error:
                    # Development is mandatory; the two following sections have
                    # deterministic normalizers so one slow provider call does
                    # not discard already validated, visible content.
                    fallback_sections.append(section)
                    completed_sections[section] = (
                        self._fallback_section_payload(
                            section,
                            request,
                            profile,
                            plan,
                            papers,
                            completed_snapshot,
                        ),
                        error.stats,
                    )
            for section in ("landscape", "learning_path"):
                section_payload, section_stats = completed_sections[section]
                apply_section(section, section_payload, section_stats)
        output = self._normalize(payload, request, profile, plan, papers)
        fallback_set = set(fallback_sections)
        output.reproducibility["generation_fallback_sections"] = [
            section
            for section in ("development", "landscape", "learning_path")
            if section in fallback_set
        ]
        return GenerationResult(output=output, stats=stats)

    def _section_payload_usable(
        self,
        section: str,
        payload: dict[str, Any],
        papers: list[RankedPaper],
    ) -> bool:
        if self._contains_embedded_structure(payload):
            return False
        allowed_ids = {paper.paper_id for paper in papers}
        if section == "development":
            stages = payload.get("development_stages") or []
            claims = payload.get("evidence_claims") or []
            return bool(
                len(str(payload.get("text") or "").strip()) >= 40
                and len(payload.get("prerequisites") or []) >= 3
                and len(stages) >= self.config.min_development_stages
                and all(
                    isinstance(stage, dict)
                    and bool(stage.get("historical_period") or stage.get("period"))
                    and bool(str(stage.get("summary") or "").strip())
                    and bool(str(stage.get("motivation") or "").strip())
                    and bool(stage.get("core_concepts"))
                    and bool(stage.get("main_techniques"))
                    and bool(stage.get("open_problems"))
                    and bool(set(stage.get("related_paper_ids") or []) & allowed_ids)
                    for stage in stages
                )
                and bool(claims)
                and all(
                    isinstance(claim, dict)
                    and bool(set(claim.get("supporting_paper_ids") or []) & allowed_ids)
                    for claim in claims
                )
            )
        if section == "landscape":
            landscape = payload.get("current_landscape")
            return bool(
                isinstance(landscape, dict)
                and len(landscape.get("problem_details") or []) >= 3
                and len(landscape.get("subdirection_details") or [])
                >= self.config.min_subdirections
            )
        steps = payload.get("learning_path") or []
        return bool(
            len(steps) >= self.config.min_learning_steps
            and all(
                isinstance(step, dict)
                and bool(str(step.get("goal") or "").strip())
                and bool(step.get("activities"))
                and bool(step.get("completion_criteria"))
                and bool(step.get("deliverables"))
                for step in steps
            )
        )

    @classmethod
    def _contains_embedded_structure(cls, value: object) -> bool:
        """Reject model artifacts that would otherwise become stringified JSON/Python."""
        if isinstance(value, str):
            text = value.strip()
            return bool(
                (text.startswith("{") and text.endswith("}"))
                or (text.startswith("[") and text.endswith("]"))
            )
        if isinstance(value, list):
            return any(cls._contains_embedded_structure(item) for item in value)
        if isinstance(value, dict):
            return any(cls._contains_embedded_structure(item) for item in value.values())
        return False

    def _fallback_section_payload(
        self,
        section: str,
        request: DomainOnboardingRequest,
        profile: LearnerProfile,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Build grounded metadata-only sections when a model call times out."""
        if section == "learning_path":
            return {"learning_path": [], "evidence_claims": []}
        if section == "landscape":
            stage_rows = [
                stage
                for stage in completed.get("development_stages", [])
                if isinstance(stage, dict) and stage.get("stage_id")
            ]
            stage_ids = [str(stage["stage_id"]) for stage in stage_rows] or [
                "stage_1",
                "stage_2",
                "stage_3",
            ]
            stage_by_paper = {
                str(paper_id): str(stage["stage_id"])
                for stage in stage_rows
                for paper_id in stage.get("related_paper_ids") or []
            }
            role_groups = (
                (
                    {"foundational"},
                    "基础范式与核心架构",
                    "基础架构在知识更新、可解释性与训练成本之间如何取舍",
                ),
                (
                    {"method"},
                    "方法改进与系统优化",
                    "方法改进如何平衡任务效果、结果可靠性与系统开销",
                ),
                (
                    {"evaluation"},
                    "评测基准与可靠性",
                    "不同数据集、指标和基线下的结论如何进行可复现比较",
                ),
                (
                    {"survey"},
                    "知识体系与工程实践",
                    "统一分类体系如何覆盖快速演进的技术与工程约束",
                ),
                (
                    {"application", "frontier"},
                    "前沿扩展与应用边界",
                    "跨任务或跨模态扩展如何避免领域偏移并保持证据可靠",
                ),
            )
            directions = []
            for roles, name, problem in role_groups:
                matches = [paper for paper in papers if paper.paper_role in roles]
                if matches:
                    directions.append((name, problem, matches[:3]))
            if len(directions) < 3:
                used_ids = {
                    paper.paper_id
                    for _, _, matches in directions
                    for paper in matches
                }
                remaining = [paper for paper in papers if paper.paper_id not in used_ids]
                if remaining:
                    directions.append(
                        ("补充问题与研究边界", "补充论文与核心问题之间的适用边界如何验证", remaining[:3])
                    )
            directions = directions[:5]
            subdirections = [name for name, _, _ in directions]
            problems = [problem for _, problem, _ in directions[:3]]
            problem_details = []
            subdirection_details = []
            claims = []
            for index, (name, problem_name, related_papers) in enumerate(directions):
                paper_ids = [paper.paper_id for paper in related_papers]
                stage_id = stage_by_paper.get(
                    paper_ids[0] if paper_ids else "", stage_ids[index % len(stage_ids)]
                )
                problem_id = f"problem_{index % len(problems) + 1}"
                subdirection_id = f"subdirection_{index + 1}"
                if index < len(problems):
                    problem_details.append(
                        {
                            "problem_id": problem_id,
                            "name": problem_name,
                            "description": f"需要结合《{related_papers[0].title}》等已检索论文的数据、指标和消融实验来回答该问题。",
                            "related_paper_ids": paper_ids,
                            "related_stage_ids": [stage_id],
                            "emerged_in_stage_id": stage_id,
                            "affected_stage_ids": [stage_id],
                            "related_subdirection_ids": [subdirection_id],
                        }
                    )
                subdirection_details.append(
                    {
                        "subdirection_id": subdirection_id,
                        "name": name,
                        "description": f"由《{related_papers[0].title}》等已验证论文覆盖，重点比较其问题设定、方法假设和实验边界。",
                        "why_it_matters": "该方向用于连接领域问题、代表方法与可复现评测。",
                        "research_questions": [f"如何为“{name}”建立可复现的对比基线？"],
                        "related_paper_ids": paper_ids,
                        "related_stage_ids": [stage_id],
                        "emerged_in_stage_id": stage_id,
                        "addresses_problem_ids": [problem_id],
                    }
                )
                claims.append(
                    {
                        "claim": f"按论文标题、摘要与角色分类，《{related_papers[0].title}》可作为“{name}”的入口阅读。",
                        "supporting_paper_ids": [related_papers[0].paper_id],
                        "support_type": "metadata_inference",
                    }
                )
            return {
                "current_landscape": {
                    "problems": problems,
                    "subdirections": subdirections,
                    "problem_details": problem_details,
                    "subdirection_details": subdirection_details,
                },
                "evidence_claims": claims,
            }

        ordered = sorted(papers, key=lambda paper: (paper.year or 9999, paper.title))
        groups: list[list[RankedPaper]] = [[], [], []]
        known_years = sorted({paper.year for paper in ordered if paper.year is not None})
        year_group = {
            year: min(2, index * 3 // max(1, len(known_years)))
            for index, year in enumerate(known_years)
        }
        for index, paper in enumerate(ordered):
            group_index = (
                year_group[paper.year]
                if paper.year is not None
                else min(2, index * 3 // max(1, len(ordered)))
            )
            groups[group_index].append(paper)
        for empty_index, group in enumerate(groups):
            if group:
                continue
            donor_index = max(range(len(groups)), key=lambda item: len(groups[item]))
            if len(groups[donor_index]) > 1:
                groups[empty_index].append(groups[donor_index].pop())
        groups.sort(
            key=lambda group: min(
                (paper.year for paper in group if paper.year is not None),
                default=9999,
            )
        )
        for index, group in enumerate(groups):
            if not group and ordered:
                group.append(ordered[min(index, len(ordered) - 1)])
        stage_names = ["基础范式形成", "方法扩展与系统化", "评测、应用与前沿推进"]
        stages = []
        claims = []
        for index, (name, group) in enumerate(zip(stage_names, groups, strict=True)):
            years = [paper.year for paper in group if paper.year is not None]
            start_year = min(years) if years else None
            end_year = max(years) if years else None
            period = (
                str(start_year)
                if start_year == end_year
                else f"{start_year or '未知'}–{end_year or '未知'}"
            )
            paper_ids = [paper.paper_id for paper in group]
            titles = "、".join(f"《{paper.title}》" for paper in group[:3])
            roles = list(dict.fromkeys(paper.paper_role for paper in group))
            role_label = "、".join(self._role_label(role) for role in roles) or "代表工作"
            stages.append(
                {
                    "stage_id": f"stage_{index + 1}",
                    "name": name,
                    "historical_period": period,
                    "start_year": start_year,
                    "end_year": end_year,
                    "summary": f"按检索到的论文年份与角色划分，该阶段由{titles}等工作代表。",
                    "motivation": f"通过{role_label}论文建立该时期的问题、方法与证据基线。",
                    "transition_from_previous": "" if index == 0 else f"在前一阶段已确立的问题与方法基础上，研究进一步转向“{name}”所覆盖的{role_label}工作。",
                    "related_paper_ids": paper_ids,
                    "core_concepts": [f"论文角色：{role}" for role in roles] or ["代表论文定位"],
                    "main_techniques": ["对比阅读论文的问题定义、方法与评测"],
                    "open_problems": [f"该时期{role_label}工作的效果边界、比较公平性与可复现性"],
                    "prerequisite_ids": [f"pre_{min(index + 1, 3)}"],
                }
            )
            evidence_paper = next(
                (paper for paper in group if (paper.abstract or "").strip()),
                group[0] if group else None,
            )
            if evidence_paper:
                abstract = " ".join((evidence_paper.abstract or "").split())
                explicit_claim = self._first_abstract_sentence(abstract)
                claims.append(
                    {
                        "claim": explicit_claim or f"该阶段的代表阅读包括《{evidence_paper.title}》。",
                        "supporting_paper_ids": [evidence_paper.paper_id],
                        "support_type": (
                            "abstract_explicit" if explicit_claim else "metadata_inference"
                        ),
                    }
                )
        return {
            "domain": self._display_domain(request, plan.normalized_domain),
            "text": f"本路线基于已验证论文的年份、角色和元数据，为“{profile.goal}”组织{self._display_domain(request, plan.normalized_domain)}的领域演进、当前方向与学习步骤。",
            "prerequisites": [
                {
                    "prerequisite_id": "pre_1",
                    "name": "机器学习与模型基础",
                    "why_needed": "用于理解代表论文的问题设定、模型结构和优化目标。",
                    "key_points": ["Transformer", "表示学习", "优化与泛化"],
                    "related_paper_ids": [paper.paper_id for paper in ordered[:2]],
                },
                {
                    "prerequisite_id": "pre_2",
                    "name": "领域问题与论文阅读",
                    "why_needed": "用于区分奠基工作、方法改进和评测论文的不同作用。",
                    "key_points": ["问题定义", "方法假设", "实验证据"],
                    "related_paper_ids": [paper.paper_id for paper in ordered[1:3]],
                },
                {
                    "prerequisite_id": "pre_3",
                    "name": "实验设计与可复现性",
                    "why_needed": "用于完成基线复现、对照实验和失败案例分析。",
                    "key_points": ["数据版本", "对照与消融", "评测指标"],
                    "related_paper_ids": [paper.paper_id for paper in ordered[-2:]],
                },
            ],
            "development_stages": stages,
            "evidence_claims": claims,
        }

    @staticmethod
    def _first_abstract_sentence(abstract: str) -> str:
        if not abstract:
            return ""
        match = re.search(r"^.{40,500}?[.!?](?:\s|$)", abstract)
        return (match.group(0) if match else abstract[:500]).strip()

    @staticmethod
    def _role_label(role: str) -> str:
        return {
            "survey": "综述",
            "foundational": "奠基",
            "method": "方法",
            "evaluation": "评测",
            "application": "应用",
            "frontier": "前沿",
            "other": "补充",
        }.get(role, "补充")

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
                "Use start_year/end_year when known; stage 1 has empty transition, later stages explain the causal transition."
            ),
            "landscape": (
                "Return current_landscape and evidence_claims. Include 3 problems and 3-5 subdirections. "
                "Each problem includes related papers, emerged_in_stage_id, affected_stage_ids and related_subdirection_ids. "
                "Each subdirection includes related papers, emerged_in_stage_id and addresses_problem_ids."
            ),
            "learning_path": (
                "Return five learning_path steps and evidence_claims. Bind foundational/survey papers early, method papers to step 3, "
                "evaluation papers to the baseline experiment step, and frontier papers only late. Each step needs deliverables; "
                "the experiment step also needs reproducibility_checklist and evaluation_metrics."
            ),
        }
        system_prompt = (
            "You generate one section of a grounded domain onboarding result. Return one JSON object only. "
            f"Write explanatory prose in {request.language}; preserve English paper titles and technical terms. "
            "Use only allowed paper IDs and stage IDs. " + instructions[section]
        )
        user_payload = {
            "request": request.model_dump(mode="json"),
            "learner_profile": profile.model_dump(mode="json"),
            "research_plan": plan.model_dump(mode="json"),
            "allowed_papers": [self._paper_prompt_payload(paper) for paper in papers],
            "completed_sections": completed,
        }
        try:
            return invoke_json(
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
        except StructuredLLMError as error:
            raise GenerationError(str(error), stats=error.stats) from error

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
            "core_concepts, main_techniques, open_problems. Use short stable stage_id values such as stage_1. "
            "current_landscape has problems:list[str], subdirections:list[str], problem_details and subdirection_details. "
            "Each problem detail has name, description, related_paper_ids and related_stage_ids. Each subdirection detail "
            "has name, description, why_it_matters, research_questions, related_paper_ids and related_stage_ids. "
            "Ground every landscape detail in allowed paper IDs and stage IDs. "
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
                    or self._fallback_contribution(paper)
                ),
                reading_focus=(
                    self._strings(guidance.get(paper.paper_id, {}).get("reading_focus"))
                    or self._fallback_reading_focus(paper)
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
        learning_path = self.path_planner.normalize(
            payload.get("learning_path"), profile=profile, papers=papers, references=references
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
            if len(text) < 40:
                preference_label = {
                    "experiment_first": "实验优先",
                    "theory_first": "理论优先",
                    "balanced": "理论与实践平衡",
                }[profile.preference]
                text = (
                    f"本方案面向{preference_label}学习偏好，按领域演进、当前全景和实践路径"
                    f"系统介绍{display_domain}，并为目标“{profile.goal}”绑定可验证论文与里程碑。"
                )
            elif request.language == "zh-CN" and plan.normalized_domain:
                text = text.replace(plan.normalized_domain, display_domain)
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
                papers=[SelectedPaper.from_ranked(paper) for paper in papers],
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
        stage_ids = [str(stage.stage_id) for stage in stages if stage.stage_id]
        problem_ids = [str(item.problem_id) for item in problem_details if item.problem_id]
        subdirection_ids = [str(item.subdirection_id) for item in subdirection_details if item.subdirection_id]
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
        for index, problem in enumerate(problem_details):
            if not problem.related_stage_ids and stage_ids:
                problem.related_stage_ids = [stage_ids[index % len(stage_ids)]]
            problem.emerged_in_stage_id = problem.emerged_in_stage_id or (problem.related_stage_ids[0] if problem.related_stage_ids else None)
            problem.affected_stage_ids = problem.affected_stage_ids or list(problem.related_stage_ids)
            problem.related_subdirection_ids = list(dict.fromkeys(
                subdirection_aliases[item]
                for item in problem.related_subdirection_ids
                if item in subdirection_aliases
            ))
            if not problem.related_subdirection_ids and subdirection_ids:
                problem.related_subdirection_ids = [subdirection_ids[index % len(subdirection_ids)]]
        for index, subdirection in enumerate(subdirection_details):
            if not subdirection.related_stage_ids and stage_ids:
                subdirection.related_stage_ids = [stage_ids[index % len(stage_ids)]]
            subdirection.emerged_in_stage_id = subdirection.emerged_in_stage_id or (subdirection.related_stage_ids[0] if subdirection.related_stage_ids else None)
            subdirection.addresses_problem_ids = list(dict.fromkeys(
                problem_aliases[item]
                for item in subdirection.addresses_problem_ids
                if item in problem_aliases
            ))
            if not subdirection.addresses_problem_ids and problem_ids:
                subdirection.addresses_problem_ids = [problem_ids[index % len(problem_ids)]]
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

    @staticmethod
    def _fallback_contribution(paper: RankedPaper) -> str:
        role_labels = {
            "survey": "综述",
            "foundational": "奠基",
            "method": "方法",
            "evaluation": "评测",
            "application": "应用",
            "frontier": "前沿",
            "other": "补充",
        }
        return f"作为{role_labels[paper.paper_role]}阅读，帮助理解《{paper.title}》所代表的研究位置与技术路线。"

    @staticmethod
    def _fallback_reading_focus(paper: RankedPaper) -> list[str]:
        focuses = ["论文解决的核心问题", "方法设计与关键假设"]
        if paper.paper_role in {"evaluation", "application"}:
            focuses[1] = "实验设置、指标与适用边界"
        return focuses

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
            refs = [references[paper_id].model_copy(deep=True) for paper_id in ids]
            results.append(
                DevelopmentStage(
                    stage_id=item.get("stage_id"),
                    sequence=index + 1,
                    name=str(item["name"]),
                    period=str(item.get("historical_period") or item.get("period") or "时期待考证"),
                    historical_period=str(item.get("historical_period") or item.get("period") or "时期待考证"),
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
            if not stage.transition_from_previous:
                stage.transition_from_previous = (
                    f"在“{previous.name}”的基础上，研究进一步转向“{stage.name}”。"
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
