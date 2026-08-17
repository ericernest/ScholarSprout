from __future__ import annotations

import unittest
from typing import Any

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.planner import StormLitePlanner
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.retrieval import (
    ArxivRetriever,
    CompositePaperRetriever,
    CrossrefRetriever,
    PaperRetrievalError,
    SemanticScholarRetriever,
)
from handlers.domain_onboarding.schemas import PaperCandidate, RetrievalResult, RetrievalStats

from .fakes import FakeJSONModel, make_candidates, make_plan, make_profile


class PlannerTests(unittest.TestCase):
    def test_plan_normalizes_common_role_and_priority_labels_without_fallback(
        self,
    ) -> None:
        payload = make_plan().model_dump(mode="json")
        payload.update(
            {
                "normalized_domain": "具身智能",
                "translated_domain": "Embodied AI",
                "expanded_terms": [
                    "embodied intelligence",
                    "embodied cognition",
                    "robot learning",
                ],
                "search_queries": [
                    "embodied AI survey",
                    "embodied AI methods",
                    "embodied AI benchmark",
                ],
            }
        )
        for branch in payload["subdirection_plans"]:
            branch["search_queries"] = [
                {
                    "query": "embodied AI methods frameworks",
                    "role_hint": "methods",
                    "path_id": branch["subdirection_id"],
                    "priority": "high",
                },
                {
                    "query": "embodied AI benchmark evaluation",
                    "role_hint": "evaluation",
                    "path_id": branch["subdirection_id"],
                    "priority": "medium",
                },
            ]

        result = StormLitePlanner(
            FakeJSONModel([payload]), DomainOnboardingConfig()
        ).plan("我想学习具身智能", make_profile())

        self.assertEqual(result.plan.planning_mode, "model")
        self.assertIsNone(result.plan.planning_fallback_reason)
        self.assertEqual(result.plan.translated_domain, "Embodied AI")
        self.assertIn("embodied intelligence", result.plan.expanded_terms)
        branch_queries = [
            query
            for branch in result.plan.subdirection_plans
            for query in branch.search_queries
        ]
        self.assertTrue(any(query.role_hint == "method" for query in branch_queries))
        self.assertTrue(any(query.priority == 1 for query in branch_queries))
        self.assertTrue(any(query.priority == 2 for query in branch_queries))

    def test_valid_plan_uses_single_model_call(self) -> None:
        model = FakeJSONModel(
            [
                {
                    "normalized_domain": "retrieval-augmented generation",
                    "perspectives": [
                        {"name": "基础", "description": "问题定义", "questions": ["是什么"]},
                        {"name": "方法", "description": "方法演进", "questions": ["怎么做"]},
                        {"name": "评测", "description": "评测前沿", "questions": ["如何评测"]},
                    ],
                    "search_queries": [
                        "retrieval augmented generation survey",
                        "RAG foundational method",
                        "RAG benchmark recent advances",
                    ],
                    "expected_subdirections": ["retrieval", "generation", "evaluation"],
                }
            ]
        )
        planner = StormLitePlanner(model, DomainOnboardingConfig())
        result = planner.plan("RAG", make_profile())
        plan = result.plan
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(plan.perspectives), 3)
        self.assertEqual(plan.translated_domain, "retrieval-augmented generation")
        self.assertIn("RAG", plan.expanded_terms)
        self.assertTrue(all(path.search_queries for path in plan.perspectives))
        self.assertTrue(any("survey" in query for query in plan.search_queries))
        self.assertEqual(plan.search_queries[0], "ARXIV:2005.11401")
        self.assertIn("ARXIV:2005.11401", plan.search_queries)
        self.assertEqual(
            plan.search_queries[1],
            "ARXIV:2310.11511",
        )
        self.assertIn("ARXIV:2309.15217", plan.search_queries)
        self.assertEqual(
            plan.search_queries,
            [query.query for query in plan.paper_queries],
        )
        self.assertTrue(
            set(planner.config.ranking_required_roles).issubset(
                {query.role_hint for query in plan.paper_queries}
            )
        )
        self.assertTrue(all(query.path_id for query in plan.paper_queries))
        self.assertEqual(
            model.calls[0]["timeout"], planner.config.planning_model_timeout_seconds
        )
        user_payload = model.calls[0]["messages"][1]["content"]
        self.assertNotIn("learner_profile", user_payload)
        self.assertNotIn("time_budget", user_payload)
        self.assertIn(
            "为每个子方向说明研究对象",
            model.calls[0]["messages"][0]["content"],
        )

    def test_planning_model_timeout_must_precede_stage_deadline(self) -> None:
        with self.assertRaises(ValueError):
            DomainOnboardingConfig(
                planning_timeout_seconds=60.0,
                planning_model_timeout_seconds=60.0,
            )

    def test_invalid_model_output_falls_back_without_fabricating_papers(self) -> None:
        planner = StormLitePlanner(FakeJSONModel(["not json"]), DomainOnboardingConfig())
        result = planner.plan("图神经网络", make_profile())
        plan = result.plan
        self.assertGreaterEqual(len(plan.perspectives), 3)
        self.assertTrue(any("graph neural networks" in query for query in plan.search_queries))
        self.assertEqual(plan.translated_domain, "graph neural networks")
        self.assertIn("GNN", plan.expanded_terms)
        self.assertTrue(all(query.path_id for query in plan.paper_queries))
        self.assertEqual(result.stats.model_calls, 1)
        self.assertEqual(result.stats.total_tokens, 50)

    def test_legacy_chinese_subdirections_gain_english_branch_queries(self) -> None:
        model = FakeJSONModel(
            [
                {
                    "normalized_domain": "检索增强生成",
                    "perspectives": [
                        {"name": "基础", "description": "问题定义", "questions": ["是什么"]},
                        {"name": "方法", "description": "方法演进", "questions": ["怎么做"]},
                        {"name": "评测", "description": "评测前沿", "questions": ["如何评测"]},
                    ],
                    "search_queries": [
                        "retrieval augmented generation survey",
                        "RAG methods",
                        "RAG evaluation",
                    ],
                    "expected_subdirections": ["理论基础", "核心方法", "评测前沿"],
                }
            ]
        )

        plan = StormLitePlanner(model, DomainOnboardingConfig()).plan(
            "检索增强生成",
            make_profile(),
        ).plan

        self.assertEqual(len(plan.subdirection_plans), 3)
        self.assertTrue(
            all(
                not any("\u4e00" <= character <= "\u9fff" for character in query.query)
                for branch in plan.subdirection_plans
                for query in branch.search_queries
            )
        )
        self.assertTrue(
            all(
                any(character.isascii() and character.isalpha() for character in branch.name_en)
                for branch in plan.subdirection_plans
            )
        )

    def test_fallback_extracts_domain_from_full_learning_request(self) -> None:
        planner = StormLitePlanner(
            FakeJSONModel([TimeoutError("planner timeout")]),
            DomainOnboardingConfig(),
        )

        plan = planner.plan(
            "我已经学过 Transformer，希望六周入门检索增强生成并复现一个基线，偏向实践",
            make_profile(),
        ).plan

        self.assertEqual(plan.normalized_domain, "检索增强生成")
        self.assertTrue(
            any("retrieval-augmented generation" in query for query in plan.search_queries)
        )
        self.assertTrue(all("我已经学过" not in query for query in plan.search_queries))

    def test_fallback_removes_request_words_and_adds_multi_agent_core_queries(self) -> None:
        planner = StormLitePlanner(FakeJSONModel(["not json"]), DomainOnboardingConfig())

        plan = planner.plan("介绍多智能体方向", make_profile()).plan

        self.assertEqual(plan.normalized_domain, "多智能体")
        self.assertTrue(any("ARXIV:2303.17760" == query for query in plan.search_queries))
        self.assertTrue(any("ARXIV:2308.08155" == query for query in plan.search_queries))
        self.assertTrue(any("multi-agent systems" in query for query in plan.search_queries))
        self.assertEqual(plan.expanded_terms, ["multi-agent systems"])
        self.assertEqual(plan.planning_mode, "fallback")
        self.assertEqual(plan.planning_fallback_reason, "structured_llm_error")

    def test_query_expansion_allocates_queries_across_paths(self) -> None:
        planner = StormLitePlanner(FakeJSONModel(["not json"]), DomainOnboardingConfig())

        plan = planner.plan("扩散模型", make_profile()).plan

        self.assertEqual(plan.translated_domain, "diffusion models")
        self.assertIn("DDPM", plan.expanded_terms)
        self.assertEqual(
            [path.path_id for path in plan.perspectives],
            ["foundations", "methods", "evaluation-frontier"],
        )
        self.assertTrue(all(path.search_queries for path in plan.perspectives))


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        error: Exception | None = None,
        text: str = "",
    ):
        self.payload = payload
        self.error = error
        self.text = text

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeHTTPClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


class RetrievalTests(unittest.TestCase):
    def test_semantic_scholar_uses_exact_arxiv_lookup_for_canonical_query(self) -> None:
        client = FakeHTTPClient(
            [
                FakeResponse(
                    {
                        "paperId": "semantic-rag",
                        "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                        "abstract": "Retrieval augmented generation combines parametric and non-parametric memory.",
                        "year": 2020,
                        "url": "https://www.semanticscholar.org/paper/semantic-rag",
                        "citationCount": 100,
                        "authors": [{"name": "Patrick Lewis"}],
                        "externalIds": {"ArXiv": "2005.11401"},
                        "publicationTypes": ["Conference"],
                    }
                )
            ]
        )

        papers = SemanticScholarRetriever(client=client).search(
            ["ARXIV:2005.11401"], limit_per_query=10
        ).papers

        self.assertEqual([paper.paper_id for paper in papers], ["semantic-rag"])
        self.assertTrue(client.calls[0]["url"].endswith("/paper/ARXIV:2005.11401"))
        self.assertNotIn("query", client.calls[0]["params"])

    def test_crossref_response_converts_to_candidate(self) -> None:
        client = FakeHTTPClient(
            [
                FakeResponse(
                    {
                        "message": {
                            "items": [
                                {
                                    "DOI": "10.1000/rag",
                                    "title": ["Grounded Retrieval"],
                                    "author": [{"given": "Ada", "family": "Lovelace"}],
                                    "published-online": {"date-parts": [[2025, 1, 2]]},
                                    "URL": "https://doi.org/10.1000/rag",
                                    "is-referenced-by-count": 7,
                                    "type": "journal-article",
                                }
                            ]
                        }
                    }
                )
            ]
        )
        papers = CrossrefRetriever(client=client).search(["RAG"], limit_per_query=2).papers
        self.assertEqual(papers[0].paper_id, "doi:10.1000/rag")
        self.assertEqual(papers[0].authors, ["Ada Lovelace"])
        self.assertEqual(papers[0].year, 2025)
        self.assertEqual(papers[0].publication_types, ["journal-article"])

    def test_crossref_filters_non_paper_work_types(self) -> None:
        client = FakeHTTPClient(
            [
                FakeResponse(
                    {
                        "message": {
                            "items": [
                                {
                                    "DOI": "10.1000/grant",
                                    "title": ["Research Grant"],
                                    "URL": "https://doi.org/10.1000/grant",
                                    "type": "grant",
                                },
                                {
                                    "DOI": "10.1000/paper",
                                    "title": ["Research Paper"],
                                    "URL": "https://doi.org/10.1000/paper",
                                    "type": "proceedings-article",
                                },
                            ]
                        }
                    }
                )
            ]
        )

        papers = CrossrefRetriever(client=client).search(["RAG"], limit_per_query=2).papers

        self.assertEqual([paper.paper_id for paper in papers], ["doi:10.1000/paper"])

    def test_arxiv_atom_feed_converts_to_candidate(self) -> None:
        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/2401.00001v2</id>
            <title> A Grounded RAG Paper </title>
            <summary> Retrieval evidence. </summary>
            <published>2024-01-02T00:00:00Z</published>
            <author><name>Ada Lovelace</name></author>
            <link rel="alternate" href="https://arxiv.org/abs/2401.00001v2" />
          </entry>
        </feed>"""
        retriever = ArxivRetriever(client=FakeHTTPClient([FakeResponse({}, text=feed)]))
        papers = retriever.search(["RAG"], limit_per_query=2).papers
        self.assertEqual(papers[0].paper_id, "arxiv:2401.00001")
        self.assertEqual(papers[0].year, 2024)
        self.assertEqual(papers[0].authors, ["Ada Lovelace"])

    def test_arxiv_uses_id_list_for_exact_canonical_query(self) -> None:
        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/2005.11401v4</id>
            <title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
            <summary>Retrieval augmented generation combines parametric and non-parametric memory.</summary>
            <published>2020-05-22T00:00:00Z</published>
            <author><name>Patrick Lewis</name></author>
          </entry>
        </feed>"""
        client = FakeHTTPClient([FakeResponse({}, text=feed)])

        papers = ArxivRetriever(client=client).search(
            ["ARXIV:2005.11401"], limit_per_query=10
        ).papers

        self.assertEqual([paper.paper_id for paper in papers], ["arxiv:2005.11401"])
        self.assertEqual(client.calls[0]["params"]["id_list"], "2005.11401")
        self.assertNotIn("search_query", client.calls[0]["params"])

    def test_semantic_scholar_response_converts_to_candidate(self) -> None:
        client = FakeHTTPClient(
            [
                FakeResponse(
                    {
                        "data": [
                            {
                                "paperId": "abc",
                                "title": "A Real Paper",
                                "abstract": "Evidence",
                                "year": 2024,
                                "url": "https://example.org/abc",
                                "citationCount": 12,
                                "authors": [{"name": "Ada"}],
                                "externalIds": {"DOI": "10.1000/abc", "ArXiv": "2401.00001"},
                                "publicationTypes": ["JournalArticle"],
                            }
                        ]
                    }
                )
            ]
        )
        retriever = SemanticScholarRetriever(client=client)
        papers = retriever.search(["query"], limit_per_query=5).papers
        self.assertEqual(papers[0].paper_id, "abc")
        self.assertEqual(papers[0].matched_queries, ["query"])
        self.assertEqual(papers[0].publication_types, ["JournalArticle"])
        self.assertEqual(client.calls[0]["params"]["limit"], 5)

    def test_single_query_failure_does_not_abort_other_queries(self) -> None:
        import httpx

        client = FakeHTTPClient(
            [
                FakeResponse({}, httpx.HTTPError("temporary")),
                FakeResponse({"data": [{"paperId": "ok", "title": "Valid", "url": "https://x/ok", "authors": []}]}),
            ]
        )
        retriever = SemanticScholarRetriever(client=client)
        result = retriever.search(["bad", "good"], limit_per_query=2)
        self.assertEqual([paper.paper_id for paper in result.papers], ["ok"])
        self.assertEqual(len(result.stats.errors), 1)

    def test_all_query_failures_raise_clear_error(self) -> None:
        import httpx

        retriever = SemanticScholarRetriever(
            client=FakeHTTPClient([FakeResponse({}, httpx.HTTPError("down"))])
        )
        with self.assertRaises(PaperRetrievalError):
            retriever.search(["a", "b"], limit_per_query=2)

    def test_composite_keeps_results_when_one_source_fails(self) -> None:
        class Failing:
            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                raise PaperRetrievalError(
                    "failed",
                    stats=RetrievalStats(errors=["rate limited"]),
                )

        class Working:
            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                return RetrievalResult(papers=make_candidates(1))

        retriever = CompositePaperRetriever([Failing(), Working()])
        result = retriever.search(["RAG"], limit_per_query=2)
        self.assertEqual(len(result.papers), 1)
        self.assertTrue(result.stats.errors)

    def test_composite_caps_and_rotates_queries_across_sources(self) -> None:
        observed: list[list[str]] = []

        class Working:
            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                observed.append(queries)
                return RetrievalResult(papers=make_candidates(1))

        result = CompositePaperRetriever(
            [Working(), Working()],
            max_queries_per_source=2,
        ).search(["q1", "q2", "q3", "q4"], limit_per_query=1)

        self.assertTrue(result.papers)
        self.assertCountEqual(observed, [["q1", "q2"], ["q3", "q4"]])
        self.assertEqual({query for batch in observed for query in batch}, {"q1", "q2", "q3", "q4"})


class RankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ranker = WeightedPaperRanker(DomainOnboardingConfig())

    def test_deduplicates_by_id_and_title(self) -> None:
        papers = make_candidates(3)
        duplicate = papers[0].model_copy(update={"paper_id": "different-id", "matched_queries": ["second"]})
        result = self.ranker.rank([*papers, duplicate], make_plan(), limit=10)
        titles = [paper.title for paper in result.papers]
        self.assertEqual(titles.count(papers[0].title), 1)
        self.assertEqual(result.stats.deduplicated_count, 3)

    def test_deduplication_merges_query_role_and_path_hints(self) -> None:
        paper = make_candidates(1)[0].model_copy(
            update={
                "matched_role_hints": ["survey"],
                "matched_path_hints": ["foundations"],
            }
        )
        duplicate = paper.model_copy(
            update={
                "paper_id": "duplicate-id",
                "matched_role_hints": ["method"],
                "matched_path_hints": ["methods"],
            }
        )

        merged = self.ranker._deduplicate([paper, duplicate])

        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0].matched_role_hints), {"survey", "method"})
        self.assertEqual(set(merged[0].matched_path_hints), {"foundations", "methods"})

    def test_invalid_url_is_filtered(self) -> None:
        invalid = PaperCandidate(
            paper_id="bad",
            title="Bad",
            url="not-a-url",
            source="fake",
        )
        result = self.ranker.rank([invalid, *make_candidates(2)], make_plan(), limit=10)
        self.assertNotIn("bad", [paper.paper_id for paper in result.papers])
        self.assertEqual(result.stats.invalid_count, 1)

    def test_scores_are_bounded_and_explainable(self) -> None:
        ranked = self.ranker.rank(make_candidates(), make_plan(), limit=6).papers
        self.assertTrue(ranked)
        for paper in ranked:
            self.assertGreaterEqual(paper.final_score, 0.0)
            self.assertLessEqual(paper.final_score, 1.0)
            self.assertIn(
                paper.paper_role,
                {"survey", "foundational", "method", "evaluation", "application", "frontier", "other"},
            )
            breakdown = paper.score_breakdown
            expected = (
                0.65 * breakdown.topic_relevance
                + 0.15 * breakdown.query_coverage
                + 0.10 * breakdown.recency
                + 0.10 * breakdown.metadata_completeness
            )
            self.assertEqual(paper.score_version, "paper-score-v2")
            self.assertAlmostEqual(paper.final_score, expected, places=5)

    def test_citation_count_does_not_affect_ranking_scores(self) -> None:
        paper = make_candidates(1)[0]
        without_citations = paper.model_copy(update={"citation_count": None})
        with_many_citations = paper.model_copy(update={"citation_count": 100_000})

        without_score = self.ranker.rank(
            [without_citations], make_plan(), limit=1
        ).papers[0]
        with_score = self.ranker.rank(
            [with_many_citations], make_plan(), limit=1
        ).papers[0]

        self.assertEqual(
            without_score.score_breakdown,
            with_score.score_breakdown,
        )
        self.assertEqual(without_score.final_score, with_score.final_score)
        self.assertEqual(without_score.score_version, "paper-score-v2")
        self.assertNotIn("citation_score", without_score.model_dump())

    def test_query_coverage_is_fraction_of_observed_retrieval_queries(self) -> None:
        papers = make_candidates(2)
        papers[0].matched_queries = ["query-a", "query-b"]
        papers[1].matched_queries = ["query-c"]

        ranked = self.ranker.rank(papers, make_plan(), limit=2).papers
        by_id = {paper.paper_id: paper for paper in ranked}

        self.assertAlmostEqual(
            by_id[papers[0].paper_id].score_breakdown.query_coverage,
            2 / 3,
            places=5,
        )
        self.assertAlmostEqual(
            by_id[papers[1].paper_id].score_breakdown.query_coverage,
            1 / 3,
            places=5,
        )

    def test_canonical_match_has_relevance_floor_when_tfidf_has_no_overlap(self) -> None:
        class ZeroVectorizer:
            name = "tfidf"

            def vectorize(self, texts):
                return [{} for _ in texts]

        ranker = WeightedPaperRanker(
            DomainOnboardingConfig(ranking_canonical_relevance_floor=0.1),
            vectorizer=ZeroVectorizer(),
        )

        paper = ranker.rank([make_candidates(1)[0]], make_plan(), limit=1).papers[0]

        self.assertTrue(paper.is_canonical)
        self.assertEqual(paper.relevance_score, 0.1)

    def test_role_classifier_does_not_treat_every_literature_review_as_survey(self) -> None:
        literature_tool = make_candidates(1)[0].model_copy(
            update={
                "paper_id": "literature-tool",
                "title": "Automated Literature Review Using Retrieval-Augmented Generation",
                "arxiv_id": None,
                "url": "https://example.org/literature-tool",
                "source": "test",
            }
        )
        image_generation = literature_tool.model_copy(
            update={
                "paper_id": "image-rag",
                "title": "Autoregressive Retrieval Augmentation for Image Generation",
                "url": "https://example.org/image-rag",
            }
        )

        ranked = self.ranker.rank(
            [literature_tool, image_generation], make_plan(), limit=2
        ).papers
        roles = {paper.paper_id: paper.paper_role for paper in ranked}

        self.assertNotEqual(roles["literature-tool"], "survey")
        self.assertEqual(roles["image-rag"], "application")

    def test_selection_caps_surveys_instead_of_padding_the_result(self) -> None:
        base = make_candidates(1)[0]
        surveys = [
            base.model_copy(
                update={
                    "paper_id": f"survey-{index}",
                    "title": f"A Comprehensive Survey of RAG Systems {index}",
                    "url": f"https://example.org/survey-{index}",
                    "arxiv_id": None,
                    "citation_count": 10 + index,
                }
            )
            for index in range(7)
        ]
        ranker = WeightedPaperRanker(
            DomainOnboardingConfig(ranking_max_survey_papers=2)
        )

        ranked = ranker.rank(surveys, make_plan(), limit=7).papers

        self.assertLessEqual(
            sum(paper.paper_role == "survey" for paper in ranked), 2
        )

    def test_noncanonical_missing_abstracts_are_dropped_when_grounded_pool_is_large(self) -> None:
        ranker = WeightedPaperRanker(
            DomainOnboardingConfig(ranking_min_abstract_candidates=5)
        )
        grounded = make_candidates(6)
        missing = grounded[0].model_copy(
            update={
                "paper_id": "missing-abstract",
                "title": "RAG Framework Without Abstract",
                "abstract": None,
                "arxiv_id": None,
                "url": "https://example.org/missing-abstract",
                "source": "test",
                "citation_count": 100000,
            }
        )

        result = ranker.rank([missing, *grounded], make_plan(), limit=10)

        self.assertNotIn("missing-abstract", [paper.paper_id for paper in result.papers])

    def test_candidate_limit_is_shared_fairly_across_sources(self) -> None:
        config = DomainOnboardingConfig(candidate_paper_limit=6, selected_paper_limit=6)
        ranker = WeightedPaperRanker(config)
        papers = [
            PaperCandidate(
                paper_id=f"{source}-{index}",
                title=f"RAG method from {source} number {index}",
                abstract="retrieval augmented generation method",
                year=2024,
                url=f"https://example.org/{source}/{index}",
                source=source,
            )
            for source, count in (("source_a", 8), ("source_b", 2), ("source_c", 2))
            for index in range(count)
        ]

        result = ranker.rank(papers, make_plan(), limit=6)

        self.assertEqual(
            result.stats.candidate_source_counts,
            {"source_a": 2, "source_b": 2, "source_c": 2},
        )

    def test_candidate_limit_preserves_each_available_query_role(self) -> None:
        ranker = WeightedPaperRanker(
            DomainOnboardingConfig(candidate_paper_limit=5, selected_paper_limit=5)
        )
        papers = [
            PaperCandidate(
                paper_id=f"{role}-{index}",
                title=f"RAG {role} paper {index}",
                abstract="retrieval augmented generation",
                year=2024,
                url=f"https://example.org/{role}/{index}",
                source="one_source",
                matched_role_hints=[role],
            )
            for role, count in (
                ("survey", 8),
                ("foundational", 1),
                ("method", 1),
                ("evaluation", 1),
                ("frontier", 1),
            )
            for index in range(count)
        ]

        limited = ranker._limit_candidates_by_source(papers, 5)

        self.assertEqual(
            {paper.matched_role_hints[0] for paper in limited},
            {"survey", "foundational", "method", "evaluation", "frontier"},
        )

    def test_role_hint_recovers_foundational_role_without_citation_count(self) -> None:
        paper = PaperCandidate(
            paper_id="hinted-foundation",
            title="Early Retrieval Augmentation Architecture",
            abstract="retrieval augmented generation",
            year=2020,
            url="https://example.org/hinted-foundation",
            source="test",
            citation_count=None,
            matched_role_hints=["foundational"],
        )

        self.assertEqual(self.ranker._classify_role(paper), "foundational")

    def test_source_specific_identity_mismatch_is_filtered(self) -> None:
        papers = [
            PaperCandidate(
                paper_id="wrong-crossref-id",
                title="Crossref Paper",
                url="https://doi.org/10.1000/paper",
                source="crossref",
                doi="10.1000/paper",
            ),
            PaperCandidate(
                paper_id="wrong-arxiv-id",
                title="arXiv Paper",
                url="https://arxiv.org/abs/2401.00001",
                source="arxiv",
                arxiv_id="2401.00001",
            ),
            PaperCandidate(
                paper_id="doi:10.1000/other",
                title="Crossref URL mismatch",
                url="https://doi.org/10.1000/different",
                source="crossref",
                doi="10.1000/other",
            ),
            PaperCandidate(
                paper_id="valid",
                title="Valid Retrieval-Augmented Generation Paper",
                year=2024,
                url="https://example.org/valid",
                source="semantic_scholar",
                publication_types=["JournalArticle"],
            ),
        ]

        result = self.ranker.rank(papers, make_plan(), limit=3)

        self.assertEqual([paper.paper_id for paper in result.papers], ["valid"])
        self.assertEqual(result.stats.invalid_count, 3)

    def test_non_paper_semantic_scholar_type_is_filtered(self) -> None:
        paper = PaperCandidate(
            paper_id="dataset-1",
            title="RAG Dataset",
            url="https://example.org/dataset-1",
            source="semantic_scholar",
            publication_types=["Dataset"],
        )

        result = self.ranker.rank([paper], make_plan(), limit=1)
        self.assertEqual(result.papers, [])
        self.assertEqual(result.stats.invalid_count, 1)

    def test_missing_publication_year_is_filtered_by_default(self) -> None:
        paper = PaperCandidate(
            paper_id="doi:10.1000/missing-year",
            title="A relevant RAG paper without a publication year",
            url="https://doi.org/10.1000/missing-year",
            source="crossref",
            doi="10.1000/missing-year",
        )

        result = self.ranker.rank([paper], make_plan(), limit=1)

        self.assertEqual(result.papers, [])
        self.assertEqual(result.stats.invalid_count, 1)

    def test_missing_year_filter_can_be_disabled_for_legacy_sources(self) -> None:
        ranker = WeightedPaperRanker(
            DomainOnboardingConfig(require_verified_paper_year=False)
        )
        paper = PaperCandidate(
            paper_id="legacy-paper",
            title="Legacy retrieval augmented generation paper",
            url="https://example.org/legacy-paper",
            source="legacy",
        )

        result = ranker.rank([paper], make_plan(), limit=1)

        self.assertEqual([item.paper_id for item in result.papers], ["legacy-paper"])


if __name__ == "__main__":
    unittest.main()
