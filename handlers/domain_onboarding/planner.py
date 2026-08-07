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
    ResearchPerspective,
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
                max_tokens=self.config.planning_max_tokens,
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
            expected_subdirections=["理论与基础", "核心方法", "评测与应用", "开放问题与前沿"],
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
        plan.search_queries = self._with_canonical_query(plan)
        return DomainResearchPlan.model_validate(plan.model_dump())

    def _with_canonical_query(self, plan: DomainResearchPlan) -> list[str]:
        specs = self.canonical_registry.specs(plan.normalized_domain)
        preferred_roles = ["foundational", "survey", "method", "evaluation"]
        ordered = sorted(
            specs,
            key=lambda spec: preferred_roles.index(spec.role) if spec.role in preferred_roles else len(preferred_roles),
        )
        exact = [
            f"ARXIV:{spec.arxiv_id}" if spec.arxiv_id else f'"{spec.title}"'
            for spec in ordered
        ]
        path_queries: list[str] = []
        max_queries = max((len(path.search_queries) for path in plan.perspectives), default=0)
        for query_index in range(max_queries):
            path_queries.extend(
                path.search_queries[query_index]
                for path in plan.perspectives
                if query_index < len(path.search_queries)
            )
        return list(dict.fromkeys([*exact, *path_queries, *plan.search_queries]))[
            : self.config.search_queries_limit
        ]
