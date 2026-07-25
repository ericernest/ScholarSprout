from __future__ import annotations

import unittest

from pydantic import ValidationError

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.profile import RuleBasedProfileBuilder
from handlers.domain_onboarding.schemas import (
    CurrentLandscape,
    DomainOnboardingRequest,
    PaperCandidate,
    Prerequisite,
    stable_id,
)


class ConfigAndSchemaTests(unittest.TestCase):
    def test_ranking_weights_must_sum_to_one(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(relevance_weight=0.9)

    def test_selected_limit_cannot_exceed_candidates(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(candidate_paper_limit=5, selected_paper_limit=6)

    def test_retrieval_max_backoff_cannot_be_smaller_than_base(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(
                retrieval_backoff_seconds=2,
                retrieval_max_backoff_seconds=1,
            )

    def test_mmr_settings_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(mmr_lambda=1.1)
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(mmr_role_bonus=0.3)

    def test_request_rejects_empty_query(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingRequest(query="  ")

    def test_future_graph_ids_are_stable(self) -> None:
        first = Prerequisite(name="线性代数")
        second = Prerequisite(name="线性代数")
        self.assertEqual(first.prerequisite_id, second.prerequisite_id)
        landscape = CurrentLandscape(subdirections=["检索优化"])
        self.assertEqual(landscape.subdirection_ids["检索优化"], stable_id("sub", "检索优化"))

    def test_paper_identifiers_are_normalized(self) -> None:
        paper = PaperCandidate(
            paper_id="paper-1",
            title="Grounded Retrieval",
            url="https://example.org/paper-1",
            source="test",
            doi="https://doi.org/10.1000/ABC",
            arxiv_id="arXiv:2401.00001v2",
            publication_types=["Journal Article", "Journal Article"],
        )
        self.assertEqual(paper.doi, "10.1000/abc")
        self.assertEqual(paper.arxiv_id, "2401.00001")
        self.assertEqual(paper.publication_types, ["Journal Article"])

    def test_paper_rejects_malformed_identifiers(self) -> None:
        with self.assertRaises(ValidationError):
            PaperCandidate(
                paper_id="paper-1",
                title="Grounded Retrieval",
                url="https://example.org/paper-1",
                source="test",
                doi="not-a-doi",
            )


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = RuleBasedProfileBuilder()

    def test_defaults_for_domain_only_request(self) -> None:
        profile = self.builder.build(DomainOnboardingRequest(query="图神经网络"))
        self.assertEqual(profile.preference, "balanced")
        self.assertIsNone(profile.time_budget_weeks)
        self.assertTrue(profile.goal)

    def test_metadata_has_priority(self) -> None:
        request = DomainOnboardingRequest(
            query="我想学习 RAG",
            metadata={
                "background": ["机器学习"],
                "goal": "完成课程项目",
                "time_budget_weeks": 8,
                "preference": "theory_first",
            },
        )
        profile = self.builder.build(request)
        self.assertEqual(profile.background, ["机器学习"])
        self.assertEqual(profile.time_budget_weeks, 8)
        self.assertEqual(profile.preference, "theory_first")

    def test_rules_parse_background_time_and_preference(self) -> None:
        request = DomainOnboardingRequest(
            query="我已经学过 Transformer，希望六周完成一个实验，偏向实践"
        )
        profile = self.builder.build(request)
        self.assertIn("Transformer", profile.background[0])
        self.assertEqual(profile.time_budget_weeks, 6)
        self.assertEqual(profile.preference, "experiment_first")


if __name__ == "__main__":
    unittest.main()
