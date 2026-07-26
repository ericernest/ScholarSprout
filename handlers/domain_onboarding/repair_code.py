"""执行不依赖模型的确定性内容规范化。"""

from __future__ import annotations

from .schemas import (
    DomainOnboardingOutput,
    PaperReference,
    RankedPaper,
    SelectedPaper,
)


class CodeRepairExecutor:
    def execute(
        self,
        output: DomainOnboardingOutput,
        allowed_papers: list[RankedPaper],
    ) -> DomainOnboardingOutput:
        repaired = output.model_copy(deep=True)
        allowed_map = {paper.paper_id: paper for paper in allowed_papers}
        allowed = set(allowed_map)
        output_ids = list(
            dict.fromkeys(
                paper.paper_id
                for paper in repaired.papers
                if paper.paper_id in allowed
            )
        )
        repaired.papers = [
            SelectedPaper.from_ranked(allowed_map[paper_id])
            for paper_id in output_ids
        ]
        for prerequisite in repaired.prerequisites:
            prerequisite.related_paper_ids = self._valid_unique(
                prerequisite.related_paper_ids,
                allowed,
            )
        for stage in repaired.development_stages:
            stage.related_paper_ids = self._valid_unique(stage.related_paper_ids, allowed)
            stage.representative_papers = [
                self._reference(allowed_map[paper_id])
                for paper_id in stage.related_paper_ids
            ]
            stage.core_concepts = self._nonempty_unique(stage.core_concepts)
            stage.main_techniques = self._nonempty_unique(stage.main_techniques)
            stage.open_problems = self._nonempty_unique(stage.open_problems)
        for index, step in enumerate(repaired.learning_path, start=1):
            step.step = str(index)
            step.paper_ids = self._valid_unique(step.paper_ids, allowed)
            step.papers = [
                self._reference(allowed_map[paper_id]) for paper_id in step.paper_ids
            ]
            step.topics = self._nonempty_unique(step.topics)
            step.activities = self._nonempty_unique(step.activities)
            step.completion_criteria = self._nonempty_unique(step.completion_criteria)
        repaired.current_landscape.problems = self._nonempty_unique(
            repaired.current_landscape.problems
        )
        repaired.current_landscape.subdirections = self._nonempty_unique(
            repaired.current_landscape.subdirections
        )
        claims_by_id = {}
        for claim in repaired.evidence_claims:
            claim.supporting_paper_ids = self._valid_unique(
                claim.supporting_paper_ids,
                allowed,
            )
            claim_id = str(claim.claim_id)
            if claim_id in claims_by_id:
                existing = claims_by_id[claim_id]
                existing.supporting_paper_ids = list(
                    dict.fromkeys(
                        [
                            *existing.supporting_paper_ids,
                            *claim.supporting_paper_ids,
                        ]
                    )
                )
                continue
            claims_by_id[claim_id] = claim
        repaired.evidence_claims = list(claims_by_id.values())
        return repaired

    @staticmethod
    def _valid_unique(values: list[str], allowed: set[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value in allowed))

    @staticmethod
    def _nonempty_unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @staticmethod
    def _reference(paper: RankedPaper) -> PaperReference:
        return PaperReference(
            paper_id=paper.paper_id,
            title=paper.title,
            authors=paper.authors,
            year=paper.year,
            url=paper.url,
        )
