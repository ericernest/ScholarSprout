from __future__ import annotations

import copy
import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.generator import StructuredOnboardingGenerator
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.repair import TargetedRepairer
from handlers.domain_onboarding.repair_code import CodeRepairExecutor
from handlers.domain_onboarding.repair_diff import (
    changed_output_paths,
    fingerprint_output,
    paths_outside_targets,
)
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    DomainOnboardingRequest,
    QualityIssue,
)

from .fakes import (
    FakeJSONModel,
    make_candidates,
    make_generation_payload,
    make_plan,
    make_profile,
)


class RepairDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DomainOnboardingConfig()
        self.ranked = WeightedPaperRanker(self.config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        self.payload = make_generation_payload(
            [paper.paper_id for paper in self.ranked]
        )
        self.output = StructuredOnboardingGenerator(
            FakeJSONModel([self.payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            self.ranked,
        ).output

    def test_diff_reports_stable_leaf_paths_and_fingerprints(self) -> None:
        repaired = self.output.model_copy(deep=True)
        repaired.learning_path[0].step = "9"

        paths = changed_output_paths(self.output, repaired)

        self.assertEqual(paths, ["$.learning_path[0].step"])
        self.assertNotEqual(
            fingerprint_output(self.output),
            fingerprint_output(repaired),
        )
        self.assertEqual(paths_outside_targets(paths, ["learning_path"]), [])
        self.assertEqual(
            paths_outside_targets(paths, ["current_landscape"]),
            paths,
        )

    def test_llm_changes_outside_issue_targets_are_discarded(self) -> None:
        repair_payload = copy.deepcopy(self.payload)
        repair_payload["domain"] = "unexpected domain rewrite"
        generator = StructuredOnboardingGenerator(
            FakeJSONModel([repair_payload]), self.config
        )
        issue = QualityIssue(
            issue_type="weak_development_stage",
            severity="warning",
            target_path="development_stages[1]",
            message="development stage is incomplete",
            recommended_action="rewrite development stage",
        )
        quality = ContentQuality(
            score=0.7,
            threshold=0.75,
            passed_hard_gates=True,
            dimensions={"development_coherence": 0.4},
            issues=[issue],
        )

        result = TargetedRepairer(generator, self.config).repair(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            self.output,
            quality,
            self.ranked,
        )

        llm_action = next(
            action for action in result.record.actions if action.action_type == "llm"
        )
        self.assertEqual(result.action, "llm_targeted_repair")
        self.assertEqual(result.output.domain, self.output.domain)
        self.assertEqual(llm_action.status, "skipped")
        self.assertEqual(llm_action.changed_paths, [])
        self.assertIsNone(llm_action.error)

    def test_code_repair_merges_duplicate_claim_evidence_without_losing_ids(self) -> None:
        expected_ids = {
            paper_id
            for claim in self.output.evidence_claims
            for paper_id in claim.supporting_paper_ids
        }

        repaired = CodeRepairExecutor().execute(self.output, self.ranked)

        actual_ids = {
            paper_id
            for claim in repaired.evidence_claims
            for paper_id in claim.supporting_paper_ids
        }
        self.assertEqual(actual_ids, expected_ids)
        self.assertEqual(
            len({str(claim.claim_id) for claim in repaired.evidence_claims}),
            len(repaired.evidence_claims),
        )


if __name__ == "__main__":
    unittest.main()
