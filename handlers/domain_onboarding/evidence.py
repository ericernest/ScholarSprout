"""校验关键论述与已验证论文之间的证据绑定。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import DomainOnboardingConfig
from .schemas import DomainOnboardingOutput, QualityIssue, RankedPaper
from .text_similarity import TextVectorizer, TfidfTextVectorizer, cosine_similarity


@dataclass(frozen=True, slots=True)
class EvidenceValidationResult:
    score: float
    hard_failure: bool
    issues: list[QualityIssue]


class ClaimEvidenceValidator:
    strong_assertion_pattern = re.compile(
        r"首次|首个|最先进|领先|证明|显著优于|state[- ]of[- ]the[- ]art|\bsota\b|\bfirst\b|\boutperform",
        re.IGNORECASE,
    )

    def __init__(
        self,
        config: DomainOnboardingConfig,
        vectorizer: TextVectorizer | None = None,
    ) -> None:
        self.config = config
        self.vectorizer = vectorizer or TfidfTextVectorizer()

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
            )

        claim_scores: list[float] = []
        cited_ids: set[str] = set()
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
            vectors = self.vectorizer.vectorize([claim.claim, *support_texts])
            similarity = max(
                (cosine_similarity(vectors[0], vector) for vector in vectors[1:]),
                default=0.0,
            )
            supported = similarity >= self.config.evidence_support_threshold
            strong = bool(self.strong_assertion_pattern.search(claim.claim))
            cross_language = self._cross_language_mismatch(claim.claim, support_texts)
            if claim.support_type == "abstract_explicit":
                claim_scores.append(1.0 if supported else (0.6 if cross_language else 0.0))
            elif claim.support_type == "metadata_inference":
                claim_scores.append(0.85 if supported else 0.4)
            else:
                claim_scores.append(0.75 if supported else 0.5)
            if not supported:
                severity = (
                    "warning"
                    if cross_language
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
                            "论述与论文为跨语言文本，当前词面校验无法确认支持强度。"
                            if cross_language
                            else "绑定论文的标题或摘要没有提供足够的词面支持。"
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
        claim_score = sum(claim_scores) / len(claim_scores) if claim_scores else 0.0
        coverage_score = sum(stage_coverage) / len(stage_coverage) if stage_coverage else 0.0
        return EvidenceValidationResult(
            score=0.75 * claim_score + 0.25 * coverage_score,
            hard_failure=hard_failure,
            issues=issues,
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
