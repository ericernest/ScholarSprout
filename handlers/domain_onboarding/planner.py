"""STORM-lite 单次领域规划器。"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Protocol

from pydantic import ValidationError

from .canonical_papers import CanonicalPaperRegistry
from .config import DomainOnboardingConfig
from .llm import StructuredLLMError, invoke_json
from .prompts import planning_system_prompt
from .schemas import (
    DomainResearchPlan,
    LearnerProfile,
    ModelCallStats,
    PlanningResult,
    PaperRole,
    PaperSearchQuery,
    ResearchPerspective,
    SubdirectionResearchPlan,
)


class DomainPlanner(Protocol):
    def plan(
        self,
        query: str,
        profile: LearnerProfile,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> PlanningResult: ...


_DOMAIN_ALIASES = {
    "多模态大模型": "multimodal large language models",
    "多智能体辩论": "multi-agent debate",
    "多智能体": "multi-agent systems",
    "多智能体系统": "multi-agent systems",
    "智能体协作": "multi-agent collaboration",
    "检索增强生成": "retrieval-augmented generation",
    "rag": "retrieval-augmented generation",
    "图神经网络": "graph neural networks",
    "扩散模型": "diffusion models",
    "大模型幻觉检测": "large language model hallucination detection",
}
_DOMAIN_CANONICAL_NAMES = {
    "rag": "检索增强生成",
}
_DOMAIN_EXPANSIONS = {
    "retrieval-augmented generation": [
        "RAG",
        "retrieval augmented generation",
        "knowledge-grounded generation",
        "dense retrieval",
        "reranking",
    ],
    "graph neural networks": ["GNN", "graph representation learning", "message passing neural networks"],
    "diffusion models": ["denoising diffusion probabilistic models", "score-based generative models"],
    "multi-agent systems": ["MAS", "agent collaboration", "agent coordination"],
    "multi-agent debate": ["multi-agent deliberation", "LLM debate", "agent collaboration"],
    "multimodal large language models": ["MLLM", "vision-language models", "multimodal foundation models"],
}


class StormLitePlanner:
    role_query_terms: dict[PaperRole, str] = {
        "survey": "survey systematic review overview",
        "foundational": "foundational seminal early work",
        "method": "methods architectures algorithms framework",
        "evaluation": "benchmark evaluation dataset metrics",
        "frontier": "recent advances open challenges 2025 2026",
        "application": "applications case study",
        "other": "research papers",
    }
    def __init__(self, model: Any, config: DomainOnboardingConfig):
        self.model = model
        self.config = config
        self.canonical_registry = CanonicalPaperRegistry()

    def plan(
        self,
        query: str,
        profile: LearnerProfile,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> PlanningResult:
        del profile
        domain_query = self._extract_domain(query)
        system_prompt = planning_system_prompt()
        user_prompt = json.dumps(
            {"domain_query": domain_query},
            ensure_ascii=False,
        )
        try:
            payload, stats = invoke_json(
                self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=self.config.planning_model_timeout_seconds,
                on_delta=on_delta,
                stream_stage="planning",
            )
            plan = DomainResearchPlan.model_validate(payload)
            if len(plan.perspectives) < 3 or not plan.search_queries:
                raise ValueError("planner coverage is insufficient")
            plan = self._expand_plan_queries(plan)
            return PlanningResult(plan=plan, stats=stats)
        except StructuredLLMError as error:
            return PlanningResult(plan=self._fallback_plan(domain_query), stats=error.stats)
        except (ValidationError, ValueError):
            return PlanningResult(plan=self._fallback_plan(domain_query), stats=stats)

    def _fallback_plan(self, query: str, profile: LearnerProfile | None = None) -> DomainResearchPlan:
        domain = self._extract_domain(query)
        english = _DOMAIN_ALIASES.get(domain.lower(), _DOMAIN_ALIASES.get(domain, domain))
        perspectives = [
            ResearchPerspective(
                path_id="foundations",
                name="理论基础与问题定义",
                description="梳理核心任务、基本假设和必要前置知识。",
                questions=["该领域解决什么问题？", "核心概念与理论基础是什么？"],
            ),
            ResearchPerspective(
                path_id="methods",
                name="方法演进与代表工作",
                description="追踪奠基工作、主要范式及其演进。",
                questions=["哪些工作奠定了研究范式？", "方法如何演进？"],
            ),
            ResearchPerspective(
                path_id="evaluation-frontier",
                name="评测、局限与前沿",
                description="关注数据集、评价方法、开放问题和近期进展。",
                questions=["如何评测？", "当前瓶颈和前沿方向是什么？"],
            ),
        ]
        queries = [
            f'"{english}" survey review',
            f'"{english}" foundational seminal paper',
            f'"{english}" methods benchmark evaluation',
            f'"{english}" recent advances 2024 2025 2026',
        ]
        plan = DomainResearchPlan(
            normalized_domain=domain,
            translated_domain=english,
            expanded_terms=self._expanded_terms(english),
            perspectives=perspectives,
            search_queries=queries[: self.config.search_queries_limit],
            expected_subdirections=["理论与基础", "核心方法", "评测与前沿"],
            subdirection_plans=[
                SubdirectionResearchPlan(
                    name_zh="理论与基础",
                    name_en="theoretical foundations and problem formulation",
                    scope="研究领域的基本任务、理论假设与问题定义。",
                    include_terms=["foundations", "problem formulation"],
                    research_questions=["What assumptions define the research problem?"],
                ),
                SubdirectionResearchPlan(
                    name_zh="核心方法",
                    name_en="core methods and architectures",
                    scope="研究实现领域核心能力的方法、模型与系统架构。",
                    include_terms=["methods", "models", "architectures"],
                    research_questions=["Which methods define the main research paradigms?"],
                ),
                SubdirectionResearchPlan(
                    name_zh="评测与前沿",
                    name_en="evaluation benchmarks and research frontiers",
                    scope="研究评测方法、数据集、开放问题与近期进展。",
                    include_terms=["evaluation", "benchmarks", "open challenges"],
                    research_questions=["How should progress and limitations be evaluated?"],
                ),
            ],
        )
        return self._expand_plan_queries(plan)

    @classmethod
    def _extract_domain(cls, query: str) -> str:
        text = query.strip()
        lowered = text.lower()
        for alias in sorted(_DOMAIN_ALIASES, key=len, reverse=True):
            if re.fullmatch(r"[a-z0-9-]+", alias):
                matched = re.search(rf"\b{re.escape(alias)}\b", lowered)
            else:
                matched = alias in text
            if matched:
                return _DOMAIN_CANONICAL_NAMES.get(alias, alias)
        match = re.search(
            r"(?:入门|学习|了解|研究)([^，。；;]{2,50}?)(?:方向|并|希望|偏(?:向|重)|$)",
            text,
        )
        if match:
            return cls._clean_domain(match.group(1))
        return cls._clean_domain(text)

    @staticmethod
    def _clean_domain(value: str) -> str:
        domain = str(value or "").strip(" \t\r\n，。！？!?：:")
        prefixes = (
            "请帮我介绍一下",
            "请帮我介绍",
            "帮我介绍一下",
            "帮我介绍",
            "请介绍一下",
            "请介绍",
            "介绍一下",
            "介绍",
            "我想入门",
            "我想学习",
            "请帮我入门",
            "帮我了解一下",
            "帮我了解",
            "了解一下",
            "了解",
            "学习",
        )
        changed = True
        while changed:
            changed = False
            for prefix in prefixes:
                if domain.startswith(prefix):
                    domain = domain.removeprefix(prefix).strip(
                        " \t\r\n，。！？!?：:"
                    )
                    changed = True
                    break
        domain = re.sub(
            r"(?:这个)?(?:研究)?(?:方向|领域|入门|概述|简介)$",
            "",
            domain,
        ).strip(" \t\r\n，。！？!?：:")
        return domain or str(value or "").strip()

    @staticmethod
    def _expanded_terms(english_domain: str) -> list[str]:
        key = english_domain.lower().strip()
        return list(dict.fromkeys([english_domain, *_DOMAIN_EXPANSIONS.get(key, [])]))

    def _expand_plan_queries(self, plan: DomainResearchPlan) -> DomainResearchPlan:
        english = plan.translated_domain.strip() or _DOMAIN_ALIASES.get(
            plan.normalized_domain.lower(),
            _DOMAIN_ALIASES.get(plan.normalized_domain, plan.normalized_domain),
        )
        plan.translated_domain = english
        plan.expanded_terms = list(
            dict.fromkeys([*plan.expanded_terms, *self._expanded_terms(english)])
        )
        templates = (
            f'"{english}" fundamentals problem definition survey',
            f'"{english}" methods architectures seminal papers',
            f'"{english}" benchmark evaluation limitations recent advances',
        )
        for index, perspective in enumerate(plan.perspectives):
            if not perspective.search_queries:
                perspective.search_queries = [
                    templates[index]
                    if index < len(templates)
                    else f'"{english}" {perspective.name} research'
                ]
        plan.paper_queries = self._build_role_queries(plan, english)
        plan.search_queries = [query.query for query in plan.paper_queries]
        plan.subdirection_plans = self._build_subdirection_plans(plan, english)
        return DomainResearchPlan.model_validate(plan.model_dump())

    @staticmethod
    def _build_subdirection_plans(
        plan: DomainResearchPlan,
        english_domain: str,
    ) -> list[SubdirectionResearchPlan]:
        branches: list[SubdirectionResearchPlan] = []
        fallback_english_names = (
            "theoretical foundations and problem formulation",
            "core methods and architectures",
            "evaluation benchmarks and research frontiers",
        )
        for index, branch in enumerate(plan.subdirection_plans[:3], start=1):
            branch_name = branch.name_en.strip()
            if not re.search(r"[A-Za-z]{3}", branch_name):
                branch_name = fallback_english_names[index - 1]
            queries = list(branch.search_queries[:2])
            if any(
                not re.search(r"[A-Za-z]{3}", query.query)
                or re.search(r"[\u4e00-\u9fff]", query.query)
                for query in queries
            ):
                queries = []
            defaults = [
                PaperSearchQuery(
                    query=(
                        f'"{english_domain}" "{branch_name}" '
                        "methods framework architecture"
                    ),
                    role_hint="method",
                    path_id=branch.subdirection_id,
                    priority=1,
                ),
                PaperSearchQuery(
                    query=(
                        f'"{english_domain}" "{branch_name}" '
                        "benchmark evaluation recent advances"
                    ),
                    role_hint="evaluation",
                    path_id=branch.subdirection_id,
                    priority=2,
                ),
            ]
            present_roles = {item.role_hint for item in queries}
            for default in defaults:
                if len(queries) >= 2:
                    break
                if default.role_hint not in present_roles:
                    queries.append(default)
                    present_roles.add(default.role_hint)
            branches.append(
                SubdirectionResearchPlan.model_validate(
                    {
                        **branch.model_dump(),
                        "subdirection_id": branch.subdirection_id or f"sub-{index}",
                        "name_en": branch_name,
                        "search_queries": [
                            query.model_dump() for query in queries[:2]
                        ],
                    }
                )
            )
        return branches

    def _build_role_queries(
        self,
        plan: DomainResearchPlan,
        english: str,
    ) -> list[PaperSearchQuery]:
        path_ids = [perspective.path_id for perspective in plan.perspectives]
        role_paths = {
            "survey": path_ids[0] if path_ids else "foundations",
            "foundational": path_ids[0] if path_ids else "foundations",
            "method": path_ids[1] if len(path_ids) > 1 else "methods",
            "evaluation": path_ids[2] if len(path_ids) > 2 else "evaluation-frontier",
            "frontier": path_ids[2] if len(path_ids) > 2 else "evaluation-frontier",
            "application": path_ids[-1] if path_ids else "applications",
            "other": "",
        }
        specs = self.canonical_registry.specs(plan.normalized_domain)
        preferred_roles = ["foundational", "survey", "method", "evaluation"]
        ordered = sorted(
            specs,
            key=lambda spec: preferred_roles.index(spec.role) if spec.role in preferred_roles else len(preferred_roles),
        )
        canonical_queries: list[PaperSearchQuery] = []
        for spec in ordered:
            canonical_queries.append(
                PaperSearchQuery(
                    query=(
                        f"ARXIV:{spec.arxiv_id}"
                        if spec.arxiv_id
                        else f'"{spec.title}"'
                    ),
                    role_hint=spec.role,
                    path_id=role_paths.get(spec.role, ""),
                    priority=1,
                )
            )

        perspective_defaults: list[PaperRole] = [
            "survey",
            "method",
            "evaluation",
        ]
        planned_queries: list[PaperSearchQuery] = []
        for index, perspective in enumerate(plan.perspectives):
            default_role = perspective_defaults[min(index, len(perspective_defaults) - 1)]
            for query in perspective.search_queries:
                role = self._infer_query_role(query, default=default_role)
                planned_queries.append(
                    PaperSearchQuery(
                        query=query,
                        role_hint=role,
                        path_id=perspective.path_id,
                        priority=2,
                    )
                )
        for query in plan.search_queries:
            role = self._infer_query_role(query, default="method")
            planned_queries.append(
                PaperSearchQuery(
                    query=query,
                    role_hint=role,
                    path_id=role_paths.get(role, ""),
                    priority=2,
                )
            )

        required_roles = list(self.config.ranking_required_roles)
        selected: list[PaperSearchQuery] = []
        for candidate in canonical_queries:
            if candidate.role_hint in required_roles and not any(
                item.role_hint == candidate.role_hint for item in selected
            ):
                selected.append(candidate)
        for role in required_roles:
            if any(item.role_hint == role for item in selected):
                continue
            planned = next(
                (item for item in planned_queries if item.role_hint == role),
                None,
            )
            selected.append(
                planned
                or PaperSearchQuery(
                    query=f'"{english}" {self.role_query_terms[role]}',
                    role_hint=role,
                    path_id=role_paths.get(role, ""),
                    priority=3,
                )
            )

        extras = [*canonical_queries, *planned_queries]
        for candidate in extras:
            if len(selected) >= self.config.search_queries_limit:
                break
            if any(item.query.casefold() == candidate.query.casefold() for item in selected):
                continue
            selected.append(candidate)
        return selected[: self.config.search_queries_limit]

    @staticmethod
    def _infer_query_role(query: str, *, default: PaperRole) -> PaperRole:
        lowered = query.lower()
        if re.search(r"survey|systematic review|overview", lowered):
            return "survey"
        if re.search(r"foundational|seminal|early work|fundamentals", lowered):
            return "foundational"
        if re.search(r"benchmark|evaluation|dataset|metric", lowered):
            return "evaluation"
        if re.search(r"recent|advance|frontier|open challenge|state of the art", lowered):
            return "frontier"
        if re.search(r"method|model|architecture|algorithm|framework", lowered):
            return "method"
        return default
