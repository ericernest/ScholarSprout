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


class CanonicalPaperRegistry:
    version = "domain-core-papers-v1"

    _DOMAINS: tuple[tuple[re.Pattern[str], tuple[CanonicalPaperSpec, ...]], ...] = (
        (
            re.compile(r"multimodal|多模态", re.IGNORECASE),
            (
                CanonicalPaperSpec("Visual Instruction Tuning", "foundational"),
                CanonicalPaperSpec("MM-Vet: Evaluating Large Multimodal Models for Integrated Capabilities", "evaluation"),
            ),
        ),
        (
            re.compile(r"multi.?agent debate|多智能体辩论", re.IGNORECASE),
            (
                CanonicalPaperSpec("Improving Factuality and Reasoning in Language Models through Multiagent Debate", "foundational"),
                CanonicalPaperSpec("ChatEval: Towards Better LLM-based Evaluators through Multi-Agent Debate", "evaluation"),
            ),
        ),
        (
            re.compile(r"retrieval.?augmented|检索增强|\brag\b", re.IGNORECASE),
            (
                CanonicalPaperSpec("Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", "foundational"),
                CanonicalPaperSpec("Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection", "method"),
                CanonicalPaperSpec("RAGAS: Automated Evaluation of Retrieval Augmented Generation", "evaluation"),
            ),
        ),
        (
            re.compile(r"graph neural|图神经网络", re.IGNORECASE),
            (
                CanonicalPaperSpec("Semi-Supervised Classification with Graph Convolutional Networks", "foundational"),
                CanonicalPaperSpec("Graph Attention Networks", "method"),
            ),
        ),
        (
            re.compile(r"diffusion model|扩散模型", re.IGNORECASE),
            (
                CanonicalPaperSpec("Denoising Diffusion Probabilistic Models", "foundational"),
                CanonicalPaperSpec("Improved Denoising Diffusion Probabilistic Models", "method"),
                CanonicalPaperSpec("High-Resolution Image Synthesis with Latent Diffusion Models", "method"),
            ),
        ),
        (
            re.compile(r"hallucination|幻觉", re.IGNORECASE),
            (
                CanonicalPaperSpec("SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models", "method"),
                CanonicalPaperSpec("HaluEval: A Large-Scale Hallucination Evaluation Benchmark for Large Language Models", "evaluation"),
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
            (spec for spec in self.specs(domain_text) if _normalize(spec.title) == title),
            None,
        )
