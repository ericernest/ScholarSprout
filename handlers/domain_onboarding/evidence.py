"""校验关键论述与已验证论文之间的证据绑定。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import DomainOnboardingConfig
from .schemas import DomainOnboardingOutput, QualityIssue, RankedPaper
from .text_similarity import (
    MultilingualEvidenceTextVectorizer,
    TextVectorizer,
    cosine_similarity,
)


@dataclass(frozen=True, slots=True)
class EvidenceValidationResult:
    score: float
    hard_failure: bool
    issues: list[QualityIssue]
    validation_modes: dict[str, int]


class ClaimEvidenceValidator:
    strong_assertion_pattern = re.compile(
        r"首次|首个|最先进|领先|证明|显著优于|主流|关键|统一.*框架|广泛应用|最新前沿|"
        r"state[- ]of[- ]the[- ]art|\bsota\b|\bfirst\b|\boutperform|\bdominant\b|\bwidely used\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        config: DomainOnboardingConfig,
        vectorizer: TextVectorizer | None = None,
    ) -> None:
        self.config = config
        self.vectorizer = vectorizer or MultilingualEvidenceTextVectorizer()
        self.fallback_vectorizer = MultilingualEvidenceTextVectorizer()

    def validate(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
    ) -> EvidenceValidationResult:
        allowed = {paper.paper_id: paper for paper in allowed_papers}
        issues: list[QualityIssue] = []
        hard_failure = False
        if not output.evidence_claims:
            return EvidenceValidationResult(
                score=0.0,
                hard_failure=True,
                issues=[
                    QualityIssue(
                        issue_type="missing_evidence",
                        severity="error",
                        target_path="evidence_claims",
                        message="关键论述没有绑定任何已验证论文。",
                        recommended_action="为发展阶段的关键论述补充候选论文 ID。",
                    )
                ],
                validation_modes={"missing": 1},
            )

        claim_scores: list[float] = []
        cited_ids: set[str] = set()
        validation_modes: dict[str, int] = {}
        for index, claim in enumerate(output.evidence_claims):
            invalid = [paper_id for paper_id in claim.supporting_paper_ids if paper_id not in allowed]
            valid_ids = [paper_id for paper_id in claim.supporting_paper_ids if paper_id in allowed]
            cited_ids.update(valid_ids)
            if invalid:
                hard_failure = True
                issues.append(
                    QualityIssue(
                        issue_type="invalid_paper",
                        severity="critical",
                        target_path=f"evidence_claims[{index}].supporting_paper_ids",
                        message=f"证据绑定包含候选集合之外的论文 ID：{invalid}。",
                        recommended_action="删除非法 ID，只使用已验证候选论文。",
                    )
                )
            if not valid_ids:
                hard_failure = True
                claim_scores.append(0.0)
                issues.append(
                    QualityIssue(
                        issue_type="missing_evidence",
                        severity="error",
                        target_path=f"evidence_claims[{index}]",
                        message="该论述没有可用的支持论文。",
                        recommended_action="绑定至少一篇能够支持该论述的候选论文。",
                    )
                )
                continue

            support_texts = [
                self._support_text(claim.support_type, allowed[paper_id])
                for paper_id in valid_ids
            ]
            active_vectorizer = self.vectorizer
            try:
                vectors = active_vectorizer.vectorize([claim.claim, *support_texts])
            except Exception:
                active_vectorizer = self.fallback_vectorizer
                vectors = active_vectorizer.vectorize([claim.claim, *support_texts])
            similarities = [
                cosine_similarity(vectors[0], vector) for vector in vectors[1:]
            ]
            supported_flags = [
                similarity >= self.config.evidence_support_threshold
                for similarity in similarities
            ]
            strong = bool(self.strong_assertion_pattern.search(claim.claim))
            cross_language = self._cross_language_mismatch(claim.claim, support_texts)
            backend = str(
                getattr(active_vectorizer, "name", type(active_vectorizer).__name__)
            ).lower()
            semantic_cross_language = cross_language and backend == "embedding"
            bridged_cross_language = (
                cross_language
                and backend == "multilingual_tfidf"
                and MultilingualEvidenceTextVectorizer.has_bridge_terms(claim.claim)
            )
            mode = (
                "multilingual_embedding"
                if semantic_cross_language
                else "terminology_bridge"
                if bridged_cross_language
                else "cross_language_unresolved"
                if cross_language
                else backend
            )
            validation_modes[mode] = validation_modes.get(mode, 0) + 1
            resolved_cross_language = semantic_cross_language or bridged_cross_language
            missing_abstract_ids = [
                paper_id
                for paper_id in valid_ids
                if claim.support_type == "abstract_explicit"
                and not (allowed[paper_id].abstract or "").strip()
            ]
            if missing_abstract_ids:
                hard_failure = True
                issues.append(
                    QualityIssue(
                        issue_type="unsupported_claim",
                        severity="error",
                        target_path=f"evidence_claims[{index}].supporting_paper_ids",
                        message=(
                            "abstract_explicit 证据缺少可验证摘要；论文 ID："
                            f"{missing_abstract_ids}。"
                        ),
                        recommended_action="补充摘要、降低证据类型或更换支持论文。",
                    )
                )
            if claim.support_type == "abstract_explicit":
                per_paper_scores = [
                    1.0 if supported and paper_id not in missing_abstract_ids else 0.0
                    for paper_id, supported in zip(valid_ids, supported_flags, strict=True)
                ]
            elif claim.support_type == "metadata_inference":
                per_paper_scores = [0.55 if supported else 0.2 for supported in supported_flags]
            else:
                per_paper_scores = [0.35 if supported else 0.15 for supported in supported_flags]
            claim_scores.append(
                sum(per_paper_scores) / len(per_paper_scores)
                if per_paper_scores
                else 0.0
            )
            unsupported_ids = [
                paper_id
                for paper_id, supported in zip(valid_ids, supported_flags, strict=True)
                if not supported
            ]
            if unsupported_ids:
                severity = (
                    "warning"
                    if cross_language and not resolved_cross_language
                    else (
                        "error"
                        if strong or claim.support_type == "abstract_explicit"
                        else "warning"
                    )
                )
                hard_failure = hard_failure or severity == "error"
                issues.append(
                    QualityIssue(
                        issue_type="unsupported_claim",
                        severity=severity,
                        target_path=f"evidence_claims[{index}].claim",
                        message=(
                            "论述与论文为跨语言文本，当前校验缺少可识别的术语桥接。"
                            if cross_language and not resolved_cross_language
                            else "跨语言语义校验后，绑定论文仍未提供足够支持。"
                            if cross_language
                            else (
                                "部分绑定论文的标题或摘要没有提供足够支持；论文 ID："
                                f"{unsupported_ids}。"
                            )
                        ),
                        recommended_action="改写论述、降低断言强度或更换支持论文。",
                    )
                )

        stage_coverage = []
        for index, stage in enumerate(output.development_stages):
            covered = bool(set(stage.related_paper_ids) & cited_ids)
            stage_coverage.append(float(covered))
            if not covered:
                issues.append(
                    QualityIssue(
                        issue_type="missing_evidence",
                        severity="warning",
                        target_path=f"development_stages[{index}]",
                        message="该发展阶段没有对应的证据论述。",
                        recommended_action="增加一条绑定该阶段代表论文的证据论述。",
                    )
                )
        landscape_coverage: list[float] = []
        landscape_items = [
            *(('problem_details', index, item) for index, item in enumerate(output.current_landscape.problem_details)),
            *(('subdirection_details', index, item) for index, item in enumerate(output.current_landscape.subdirection_details)),
        ]
        for field, index, item in landscape_items:
            related_ids = set(item.related_paper_ids)
            covered = bool(related_ids and related_ids & cited_ids)
            landscape_coverage.append(float(covered))
            if not covered:
                issues.append(
                    QualityIssue(
                        issue_type="missing_evidence",
                        severity="warning",
                        target_path=f"current_landscape.{field}[{index}]",
                        message="该领域全景项没有与已验证论文证据声明建立关联。",
                        recommended_action="补充相关论文 ID，并增加一条可验证的证据论述。",
                    )
                )
        claim_score = sum(claim_scores) / len(claim_scores) if claim_scores else 0.0
        coverage_values = [*stage_coverage, *landscape_coverage]
        coverage_score = (
            sum(coverage_values) / len(coverage_values)
            if coverage_values
            else 0.0
        )
        return EvidenceValidationResult(
            score=0.75 * claim_score + 0.25 * coverage_score,
            hard_failure=hard_failure,
            issues=issues,
            validation_modes=validation_modes,
        )

    @staticmethod
    def _support_text(support_type: str, paper: RankedPaper) -> str:
        if support_type == "metadata_inference":
            return f"{paper.title} {paper.year or ''}"
        return f"{paper.title} {paper.abstract or ''}"

    @staticmethod
    def _cross_language_mismatch(claim: str, support_texts: list[str]) -> bool:
        claim_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", claim))
        support_has_cjk = bool(
            re.search(r"[\u4e00-\u9fff]", " ".join(support_texts))
        )
        return claim_has_cjk != support_has_cjk
