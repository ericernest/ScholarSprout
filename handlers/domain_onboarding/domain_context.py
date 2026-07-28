"""Deterministic domain-context checks for ambiguous academic terminology."""

from __future__ import annotations

import re

from .schemas import DomainResearchPlan, PaperCandidate


class DomainContextGuard:
    """Reject known same-token/different-field papers before generation.

    Embeddings are intentionally not used here: this layer handles high-risk
    academic homonyms where semantic proximity is itself misleading.
    """

    _DIFFUSION_DOMAIN = re.compile(
        r"diffusion model|denoising diffusion|score[- ]based|扩散模型",
        re.IGNORECASE,
    )
    _GENERATIVE_DIFFUSION = re.compile(
        r"denoising diffusion|diffusion probabilistic|\bddpm\b|\bddim\b|"
        r"score[- ]based|latent diffusion|image (?:generation|synthesis)|"
        r"text[- ]to[- ]image|generative",
        re.IGNORECASE,
    )
    _NON_GENERATIVE_DIFFUSION = re.compile(
        r"diffusion mri|magnetic resonance|microstructure|wiener diffusion|"
        r"first[- ]passage|drift diffusion|reaction[- ]diffusion|"
        r"molecular diffusion|heat diffusion|mass diffusion",
        re.IGNORECASE,
    )

    def score(self, paper: PaperCandidate, plan: DomainResearchPlan) -> float:
        domain_text = " ".join(
            [plan.normalized_domain, *plan.search_queries, *plan.expected_subdirections]
        )
        paper_text = f"{paper.title} {paper.abstract or ''}"
        if not self._DIFFUSION_DOMAIN.search(domain_text):
            return 1.0
        if self._NON_GENERATIVE_DIFFUSION.search(
            paper_text
        ) and not self._GENERATIVE_DIFFUSION.search(paper_text):
            return 0.0
        return 1.0

