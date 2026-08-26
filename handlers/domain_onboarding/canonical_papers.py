"""Small versioned registry of core readings for the six evaluation domains."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import PaperCandidate, PaperRole


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


@dataclass(frozen=True, slots=True)
class CanonicalPaperSpec:
    title: str
    role: PaperRole
    arxiv_id: str | None = None


class CanonicalPaperRegistry:
    version = "domain-core-papers-v1"

    _DOMAINS: tuple[tuple[re.Pattern[str], tuple[CanonicalPaperSpec, ...]], ...] = (
        (
            re.compile(r"multimodal|多模态", re.IGNORECASE),
            (
                CanonicalPaperSpec("Visual Instruction Tuning", "foundational", "2304.08485"),
                CanonicalPaperSpec("MM-Vet: Evaluating Large Multimodal Models for Integrated Capabilities", "evaluation", "2308.02490"),
            ),
        ),
        (
            re.compile(r"multi.?agent debate|多智能体辩论", re.IGNORECASE),
            (
                CanonicalPaperSpec("Improving Factuality and Reasoning in Language Models through Multiagent Debate", "foundational", "2305.14325"),
                CanonicalPaperSpec("ChatEval: Towards Better LLM-based Evaluators through Multi-Agent Debate", "evaluation", "2308.07201"),
            ),
        ),
        (
            re.compile(r"multi.?agent|多智能体|智能体协作", re.IGNORECASE),
            (
                CanonicalPaperSpec("CAMEL: Communicative Agents for Mind Exploration of Large Scale Language Model Society", "foundational", "2303.17760"),
                CanonicalPaperSpec("AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation", "method", "2308.08155"),
                CanonicalPaperSpec("MetaGPT: Meta Programming for Multi-Agent Collaborative Framework", "method", "2308.00352"),
            ),
        ),
        (
            re.compile(r"retrieval.?augmented|检索增强|\brag\b", re.IGNORECASE),
            (
                CanonicalPaperSpec("Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", "foundational", "2005.11401"),
                CanonicalPaperSpec("Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection", "method", "2310.11511"),
                CanonicalPaperSpec("RAGAS: Automated Evaluation of Retrieval Augmented Generation", "evaluation", "2309.15217"),
            ),
        ),
        (
            re.compile(r"graph neural|图神经网络", re.IGNORECASE),
            (
                CanonicalPaperSpec("Semi-Supervised Classification with Graph Convolutional Networks", "foundational", "1609.02907"),
                CanonicalPaperSpec("Graph Attention Networks", "method", "1710.10903"),
            ),
        ),
        (
            re.compile(r"diffusion model|扩散模型", re.IGNORECASE),
            (
                CanonicalPaperSpec("Denoising Diffusion Probabilistic Models", "foundational", "2006.11239"),
                CanonicalPaperSpec("Improved Denoising Diffusion Probabilistic Models", "method", "2102.09672"),
                CanonicalPaperSpec("High-Resolution Image Synthesis with Latent Diffusion Models", "method", "2112.10752"),
            ),
        ),
        (
            re.compile(r"hallucination|幻觉", re.IGNORECASE),
            (
                CanonicalPaperSpec("SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models", "method", "2303.08896"),
                CanonicalPaperSpec("HaluEval: A Large-Scale Hallucination Evaluation Benchmark for Large Language Models", "evaluation", "2305.11747"),
            ),
        ),
    )

    def specs(self, domain_text: str) -> tuple[CanonicalPaperSpec, ...]:
        for pattern, specs in self._DOMAINS:
            if pattern.search(domain_text):
                return specs
        return ()

    def match(self, paper: PaperCandidate, domain_text: str) -> CanonicalPaperSpec | None:
        title = _normalize(paper.title)
        return next(
            (
                spec
                for spec in self.specs(domain_text)
                if _normalize(spec.title) == title
                or (
                    spec.arxiv_id is not None
                    and paper.arxiv_id is not None
                    and spec.arxiv_id.lower() == paper.arxiv_id.lower()
                )
            ),
            None,
        )
