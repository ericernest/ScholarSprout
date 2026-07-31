"""STORM-lite 单次领域规划器。"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from pydantic import ValidationError

from .canonical_papers import CanonicalPaperRegistry
from .config import DomainOnboardingConfig
from .llm import StructuredLLMError, invoke_json
from .schemas import (
    DomainResearchPlan,
    LearnerProfile,
    ModelCallStats,
    PlanningResult,
    ResearchPerspective,
)


class DomainPlanner(Protocol):
    def plan(self, query: str, profile: LearnerProfile) -> PlanningResult: ...


_DOMAIN_ALIASES = {
    "多模态大模型": "multimodal large language models",
    "多智能体辩论": "multi-agent debate",
    "检索增强生成": "retrieval-augmented generation",
    "rag": "retrieval-augmented generation",
    "图神经网络": "graph neural networks",
    "扩散模型": "diffusion models",
    "大模型幻觉检测": "large language model hallucination detection",
}
_DOMAIN_CANONICAL_NAMES = {
    "rag": "检索增强生成",
}


class StormLitePlanner:
    def __init__(self, model: Any, config: DomainOnboardingConfig):
        self.model = model
        self.config = config
        self.canonical_registry = CanonicalPaperRegistry()

    def plan(self, query: str, profile: LearnerProfile) -> PlanningResult:
        system_prompt = (
            "You are a STORM-lite research planner. Return one JSON object only. "
            "Decompose the domain from multiple research perspectives before retrieval. "
            "Schema: normalized_domain:string, perspectives:[{name,description,questions:[string]}], "
            "search_queries:[string], expected_subdirections:[string]. "
            "Create at least three non-duplicate perspectives. Search queries must include English "
            "technical terms and cover survey, foundational work, methods, evaluation and recent progress."
        )
        user_prompt = json.dumps(
            {"query": query, "learner_profile": profile.model_dump(mode="json")},
            ensure_ascii=False,
        )
        try:
            payload, stats = invoke_json(
                self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self.config.planning_max_tokens,
                timeout_seconds=self.config.planning_timeout_seconds,
            )
            plan = DomainResearchPlan.model_validate(payload)
            if len(plan.perspectives) < 3 or not plan.search_queries:
                raise ValueError("planner coverage is insufficient")
            plan.search_queries = self._with_canonical_query(plan, profile)
            return PlanningResult(plan=plan, stats=stats)
        except StructuredLLMError as error:
            return PlanningResult(plan=self._fallback_plan(query, profile), stats=error.stats)
        except (ValidationError, ValueError):
            return PlanningResult(plan=self._fallback_plan(query, profile), stats=stats)

    def _fallback_plan(self, query: str, profile: LearnerProfile | None = None) -> DomainResearchPlan:
        domain = self._extract_domain(query)
        english = _DOMAIN_ALIASES.get(domain.lower(), _DOMAIN_ALIASES.get(domain, domain))
        perspectives = [
            ResearchPerspective(
                name="理论基础与问题定义",
                description="梳理核心任务、基本假设和必要前置知识。",
                questions=["该领域解决什么问题？", "核心概念与理论基础是什么？"],
            ),
            ResearchPerspective(
                name="方法演进与代表工作",
                description="追踪奠基工作、主要范式及其演进。",
                questions=["哪些工作奠定了研究范式？", "方法如何演进？"],
            ),
            ResearchPerspective(
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
            perspectives=perspectives,
            search_queries=queries[: self.config.search_queries_limit],
            expected_subdirections=["理论与基础", "核心方法", "评测与应用", "开放问题与前沿"],
        )
        plan.search_queries = self._with_canonical_query(plan, profile or LearnerProfile())
        return plan

    @staticmethod
    def _extract_domain(query: str) -> str:
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
            return match.group(1).strip()
        for prefix in ("我想入门", "我想学习", "请帮我入门", "学习"):
            text = text.removeprefix(prefix).strip()
        return text.removesuffix("方向").strip() or query.strip()

    def _with_canonical_query(
        self, plan: DomainResearchPlan, profile: LearnerProfile
    ) -> list[str]:
        specs = self.canonical_registry.specs(plan.normalized_domain)
        goal_text = f"{profile.goal} {profile.preference}".lower()
        preferred_roles = (
            ["evaluation", "method", "foundational"]
            if profile.preference == "experiment_first" or any(term in goal_text for term in ("实验", "复现", "基线", "benchmark"))
            else ["foundational", "survey", "method", "evaluation"]
        )
        ordered = sorted(
            specs,
            key=lambda spec: preferred_roles.index(spec.role) if spec.role in preferred_roles else len(preferred_roles),
        )
        exact = [
            f"ARXIV:{spec.arxiv_id}" if spec.arxiv_id else f'"{spec.title}"'
            for spec in ordered
        ]
        return list(dict.fromkeys([*exact, *plan.search_queries]))[
            : self.config.search_queries_limit
        ]
