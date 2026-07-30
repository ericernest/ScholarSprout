from __future__ import annotations

import json
from typing import Any

from handlers.domain_onboarding.schemas import (
    DomainResearchPlan,
    LearnerProfile,
    PaperCandidate,
    ResearchPerspective,
)


class FakeJSONModel:
    def __init__(self, responses: list[dict[str, Any] | str | Exception]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        value = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(value, Exception):
            raise value
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return {
            "choices": [{"message": {"content": content, "tool_calls": []}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
        }


def make_profile(preference: str = "balanced") -> LearnerProfile:
    return LearnerProfile(
        background=["Python", "Transformer"],
        goal="建立知识框架并完成基线实验",
        time_budget_weeks=6,
        preference=preference,
    )


def make_plan() -> DomainResearchPlan:
    return DomainResearchPlan(
        normalized_domain="检索增强生成",
        perspectives=[
            ResearchPerspective(name="基础", description="理论基础", questions=["是什么"]),
            ResearchPerspective(name="方法", description="主要方法", questions=["怎么做"]),
            ResearchPerspective(name="评测", description="评测与前沿", questions=["如何评测"]),
        ],
        search_queries=["retrieval augmented generation survey", "RAG benchmark evaluation"],
        expected_subdirections=["检索", "生成", "评测"],
    )


def make_candidates(count: int = 6) -> list[PaperCandidate]:
    titles = [
        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        "A Survey on Retrieval-Augmented Text Generation",
        "Benchmarking Retrieval-Augmented Generation",
        "Dense Passage Retrieval for Open-Domain Question Answering",
        "Self-RAG: Learning to Retrieve, Generate, and Critique",
        "Corrective Retrieval Augmented Generation",
    ]
    return [
        PaperCandidate(
            paper_id=f"paper-{index}",
            title=titles[index % len(titles)],
            authors=[f"Author {index}"],
            abstract="retrieval augmented generation method benchmark evaluation",
            year=2020 + index,
            url=f"https://www.semanticscholar.org/paper/paper-{index}",
            citation_count=1000 // (index + 1),
            source="semantic_scholar",
            matched_queries=["retrieval augmented generation"],
            arxiv_id=f"20{index:02d}.00001",
        )
        for index in range(count)
    ]


def make_generation_payload(paper_ids: list[str]) -> dict[str, Any]:
    return {
        "domain": "检索增强生成",
        "text": "检索增强生成通过外部知识检索增强语言模型生成，入门需要理解检索、生成、评测与工程实践之间的联系。",
        "prerequisites": [
            {"name": "信息检索", "why_needed": "理解召回与排序", "key_points": ["BM25", "向量检索"]},
            {"name": "自然语言处理", "why_needed": "理解生成模型", "key_points": ["Transformer"]},
            {"name": "机器学习评测", "why_needed": "评价端到端效果", "key_points": ["离线指标"]},
        ],
        "development_stages": [
            {
                "stage_id": f"stage-{index}",
                "sequence": index,
                "name": f"阶段 {index}",
                "period": f"时期 {index}",
                "summary": "形成代表性研究范式",
                "motivation": "解决知识更新和事实可靠性问题",
                "transition_from_previous": (
                    ""
                    if index == 1
                    else f"阶段 {index - 1} 的局限推动了阶段 {index}。"
                ),
                "related_paper_ids": [paper_ids[(index - 1) % len(paper_ids)]],
                "core_concepts": ["检索增强"],
                "main_techniques": ["检索与生成"],
                "open_problems": ["事实一致性"],
            }
            for index in range(1, 4)
        ],
        "current_landscape": {
            "problems": ["检索噪声", "证据冲突", "端到端评测"],
            "subdirections": ["检索优化", "生成约束", "自动评测"],
            "problem_details": [
                {
                    "name": name,
                    "description": f"{name}会限制 RAG 的可靠性。",
                    "related_paper_ids": [paper_ids[index % len(paper_ids)]],
                    "related_stage_ids": [f"stage-{index % 3 + 1}"],
                }
                for index, name in enumerate(["检索噪声", "证据冲突", "端到端评测"])
            ],
            "subdirection_details": [
                {
                    "name": name,
                    "description": f"{name}是当前可证据化的技术方向。",
                    "why_it_matters": "直接影响召回、生成或评测质量。",
                    "research_questions": [f"如何稳定改善{name}？"],
                    "related_paper_ids": [paper_ids[index % len(paper_ids)]],
                    "related_stage_ids": [f"stage-{index % 3 + 1}"],
                }
                for index, name in enumerate(["检索优化", "生成约束", "自动评测"])
            ],
        },
        "learning_path": [
            {
                "step": index,
                "goal": f"完成阶段 {index}",
                "topics": ["RAG"],
                "paper_ids": [paper_ids[(index - 1) % len(paper_ids)]],
                "activities": ["阅读论文并完成实验"],
                "completion_criteria": ["形成可检查的笔记或实验结果"],
                "expected_outcome": "能够解释并实现核心方法",
                "estimated_hours": 6,
                "milestone": f"完成阶段 {index} 的可验收产出",
                "deliverables": [f"阶段 {index} 结构化笔记"],
                "reproducibility_checklist": (
                    ["固定数据集版本", "保存运行配置"] if index == 4 else []
                ),
                "evaluation_metrics": (
                    ["任务主指标", "运行成本"] if index == 4 else []
                ),
            }
            for index in range(1, 6)
        ],
        "evidence_claims": [
            {
                "claim": "retrieval augmented generation method benchmark evaluation",
                "supporting_paper_ids": [paper_ids[(index - 1) % len(paper_ids)]],
                "support_type": "abstract_explicit",
            }
            for index in range(1, 4)
        ],
        "paper_guidance": [
            {
                "paper_id": paper_id,
                "contribution": f"说明论文 {paper_id} 在领域发展与学习路径中的作用。",
                "reading_focus": ["核心问题", "方法设计", "实验结论"],
            }
            for paper_id in paper_ids
        ],
    }
