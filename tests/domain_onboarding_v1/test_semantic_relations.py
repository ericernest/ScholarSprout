from __future__ import annotations

import unittest

from handlers.domain_onboarding.relations import SemanticRelationResolver
from handlers.domain_onboarding.schemas import (
    CurrentLandscape,
    DevelopmentStage,
    LandscapeProblem,
    StageBreakthrough,
    SubdirectionDetail,
)

from .fakes import make_candidates, make_plan
from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.ranking import WeightedPaperRanker


class SemanticRelationResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.papers = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            make_candidates(), make_plan(), limit=6
        ).papers

    def test_problem_stage_links_use_all_supporting_paper_memberships(self) -> None:
        paper_id = self.papers[0].paper_id
        stages = [
            DevelopmentStage(
                stage_id=f"stage-{index}",
                sequence=index,
                name=f"阶段 {index}",
                related_paper_ids=[paper_id],
            )
            for index in range(1, 4)
        ]
        problem = LandscapeProblem(
            problem_id="problem-1",
            name="证据噪声",
            related_paper_ids=[paper_id],
        )
        landscape = CurrentLandscape(problem_details=[problem])

        SemanticRelationResolver().resolve(stages, landscape, self.papers)

        self.assertEqual(problem.related_stage_ids, ["stage-1", "stage-2", "stage-3"])
        self.assertEqual(problem.emerged_in_stage_id, "stage-1")
        self.assertEqual(problem.affected_stage_ids, ["stage-1", "stage-2", "stage-3"])
        self.assertEqual(problem.relation_status, "paper_inferred")

    def test_semantic_links_are_bidirectional_without_index_wraparound(self) -> None:
        problems = [
            LandscapeProblem(
                problem_id="problem-1",
                name="检索噪声",
                description="低质量文档会污染检索结果。",
            ),
            LandscapeProblem(
                problem_id="problem-2",
                name="证据冲突",
                description="多个来源可能相互矛盾。",
            ),
            LandscapeProblem(
                problem_id="problem-3",
                name="评测不足",
                description="需要可靠的评价指标和基准。",
            ),
        ]
        directions = [
            SubdirectionDetail(
                subdirection_id="direction-1",
                name="检索去噪",
                description="通过重排序降低检索噪声。",
            ),
            SubdirectionDetail(
                subdirection_id="direction-4",
                name="量子色动力学",
                description="研究强相互作用的规范场论。",
            ),
        ]
        landscape = CurrentLandscape(
            problem_details=problems,
            subdirection_details=directions,
        )

        SemanticRelationResolver().resolve([], landscape, self.papers)

        self.assertEqual(directions[0].addresses_problem_ids, ["problem-1"])
        self.assertEqual(problems[0].related_subdirection_ids, ["direction-1"])
        self.assertEqual(directions[0].relation_status, "semantic_inferred")
        self.assertEqual(directions[1].addresses_problem_ids, [])
        self.assertNotIn("direction-4", problems[0].related_subdirection_ids)
        self.assertEqual(directions[1].relation_status, "unresolved")

    def test_breakthrough_links_to_stage_limitations(self) -> None:
        paper_id = self.papers[0].paper_id
        breakthrough = StageBreakthrough(
            breakthrough_id="breakthrough-1",
            name="非参数记忆",
            description="通过检索引入外部知识。",
            supporting_paper_ids=[paper_id],
        )
        stage = DevelopmentStage(
            stage_id="stage-1",
            name="经典 RAG",
            breakthroughs=[breakthrough],
            related_paper_ids=[paper_id],
        )
        problem = LandscapeProblem(
            problem_id="problem-1",
            name="检索噪声",
            related_paper_ids=[paper_id],
        )
        landscape = CurrentLandscape(problem_details=[problem])

        SemanticRelationResolver().resolve([stage], landscape, self.papers)

        self.assertEqual(stage.related_problem_ids, ["problem-1"])
        self.assertEqual(breakthrough.limitation_problem_ids, ["problem-1"])
        self.assertEqual(breakthrough.relation_status, "paper_inferred")

    def test_relation_schema_round_trip_preserves_ids_and_status(self) -> None:
        stage = DevelopmentStage(
            stage_id="stage-1",
            name="经典 RAG",
            related_problem_ids=["problem-1"],
            breakthroughs=[
                StageBreakthrough(
                    breakthrough_id="breakthrough-1",
                    name="检索增强",
                    description="外部知识进入生成过程。",
                    limitation_problem_ids=["problem-1"],
                    relation_status="explicit",
                )
            ],
        )

        restored = DevelopmentStage.model_validate_json(stage.model_dump_json())

        self.assertEqual(restored, stage)
        self.assertEqual(restored.breakthroughs[0].relation_status, "explicit")


if __name__ == "__main__":
    unittest.main()
