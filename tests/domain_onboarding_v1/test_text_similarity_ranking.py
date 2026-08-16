from __future__ import annotations

import unittest
from unittest.mock import patch

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.pipeline import create_default_pipeline
from handlers.domain_onboarding.schemas import DomainResearchPlan, PaperCandidate, ResearchPerspective
from handlers.domain_onboarding.text_similarity import (
    CachedEmbeddingTextVectorizer,
    FastEmbedProvider,
    MultilingualEvidenceTextVectorizer,
    OpenAIEmbeddingProvider,
    TfidfTextVectorizer,
    cosine_similarity,
)

from .fakes import make_plan


class TextSimilarityTests(unittest.TestCase):
    def test_fastembed_adapter_converts_local_vectors(self) -> None:
        class Vector:
            def __init__(self, values: list[float]) -> None:
                self.values = values

            def tolist(self) -> list[float]:
                return self.values

        class Encoder:
            def embed(self, texts: list[str]):
                return (
                    Vector([float(index), 1.0])
                    for index, _ in enumerate(texts)
                )

        provider = FastEmbedProvider("multilingual-test", encoder=Encoder())

        self.assertEqual(
            provider.embed(["中文", "English"]),
            [[0.0, 1.0], [1.0, 1.0]],
        )

    def test_tfidf_cosine_prefers_related_text(self) -> None:
        vectors = TfidfTextVectorizer().vectorize(
            [
                "retrieval augmented generation",
                "retrieval augmented generation with evidence",
                "graph convolutional networks",
            ]
        )

        self.assertGreater(
            cosine_similarity(vectors[0], vectors[1]),
            cosine_similarity(vectors[0], vectors[2]),
        )

    def test_multilingual_lexical_fallback_bridges_chinese_domain_terms(self) -> None:
        vectors = MultilingualEvidenceTextVectorizer().vectorize(
            [
                "retrieval augmented generation",
                "检索增强生成与外部知识问答",
                "古代园林建筑艺术研究",
            ]
        )

        self.assertGreater(
            cosine_similarity(vectors[0], vectors[1]),
            cosine_similarity(vectors[0], vectors[2]),
        )

    def test_openai_embedding_adapter_preserves_multilingual_semantics(self) -> None:
        class Model:
            def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
                self.model = model
                mapping = {
                    "检索增强生成": [1.0, 0.0],
                    "retrieval augmented generation": [1.0, 0.0],
                    "graph neural networks": [0.0, 1.0],
                }
                return [mapping[text] for text in texts]

        model = Model()
        vectorizer = CachedEmbeddingTextVectorizer(
            OpenAIEmbeddingProvider(model, "multilingual-embedding")
        )
        vectors = vectorizer.vectorize(
            ["检索增强生成", "retrieval augmented generation", "graph neural networks"]
        )

        self.assertEqual(model.model, "multilingual-embedding")
        self.assertEqual(cosine_similarity(vectors[0], vectors[1]), 1.0)
        self.assertEqual(cosine_similarity(vectors[0], vectors[2]), 0.0)

    def test_openai_embedding_adapter_forwards_bounded_timeout(self) -> None:
        class Model:
            def embed(
                self,
                texts: list[str],
                *,
                model: str,
                timeout: float | None = None,
            ) -> list[list[float]]:
                self.call = (model, timeout)
                return [[1.0] for _ in texts]

        model = Model()
        provider = OpenAIEmbeddingProvider(
            model,
            "qwen3-embedding",
            timeout_seconds=30.0,
        )

        self.assertEqual(provider.embed(["RAG"]), [[1.0]])
        self.assertEqual(model.call, ("qwen3-embedding", 30.0))

    def test_default_pipeline_uses_qwen3_embedding_by_default(self) -> None:
        class Model:
            def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
                return [[1.0] for _ in texts]

        with patch.dict("os.environ", {}, clear=True):
            pipeline = create_default_pipeline(Model())
        try:
            self.assertEqual(
                pipeline.ranker.vectorizer.name,
                "embedding:qwen3-embedding",
            )
            self.assertIs(
                pipeline.coverage_analyzer.vectorizer,
                pipeline.ranker.vectorizer,
            )
        finally:
            pipeline.close()

    def test_default_pipeline_uses_configured_embedding_client_and_model(self) -> None:
        class ChatModel:
            pass

        class EmbeddingModel:
            def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
                return [[1.0] for _ in texts]

        embedding_model = EmbeddingModel()
        with patch.dict("os.environ", {}, clear=True):
            pipeline = create_default_pipeline(
                ChatModel(),
                embedding_model=embedding_model,
                embedding_model_name="configured-embedding",
            )
        try:
            provider = pipeline.ranker.vectorizer.provider
            self.assertIs(provider.model, embedding_model)
            self.assertEqual(provider.embedding_model, "configured-embedding")
        finally:
            pipeline.close()

    def test_default_pipeline_can_disable_embeddings(self) -> None:
        with patch.dict(
            "os.environ",
            {"DOMAIN_ONBOARDING_EMBEDDING_ENABLED": "false"},
            clear=True,
        ):
            pipeline = create_default_pipeline(object())
        try:
            self.assertEqual(pipeline.ranker.vectorizer.name, "multilingual_tfidf")
        finally:
            pipeline.close()

    def test_default_pipeline_prefers_local_embedding_model(self) -> None:
        class Provider:
            def __init__(self, model_name: str, *, cache_dir: str | None = None) -> None:
                self.model_name = model_name
                self.cache_dir = cache_dir

            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0] for _ in texts]

        with (
            patch(
                "handlers.domain_onboarding.pipeline.FastEmbedProvider",
                Provider,
            ),
            patch.dict(
                "os.environ",
                {
                    "DOMAIN_ONBOARDING_LOCAL_EMBEDDING_MODEL": "local-multilingual",
                    "DOMAIN_ONBOARDING_EMBEDDING_MODEL": "remote-model",
                    "DOMAIN_ONBOARDING_EMBEDDING_CACHE_DIR": "/tmp/embedding-cache",
                },
            ),
        ):
            pipeline = create_default_pipeline(object())
        try:
            provider = pipeline.ranker.vectorizer.provider
            self.assertEqual(provider.model_name, "local-multilingual")
            self.assertEqual(provider.cache_dir, "/tmp/embedding-cache")
        finally:
            pipeline.close()


class MMRRankingTests(unittest.TestCase):
    def test_generic_academic_terms_cannot_replace_domain_relevance(self) -> None:
        plan = DomainResearchPlan(
            normalized_domain="图神经网络",
            translated_domain="graph neural networks",
            expanded_terms=["message passing", "node classification"],
            perspectives=[
                ResearchPerspective(
                    path_id="foundations",
                    name="基础",
                    description="图结构与表示学习基础",
                    questions=["核心问题是什么？"],
                ),
                ResearchPerspective(
                    path_id="methods",
                    name="方法",
                    description="消息传递与训练方法",
                    questions=["如何训练？"],
                ),
                ResearchPerspective(
                    path_id="evaluation",
                    name="评测",
                    description="节点分类基准与指标",
                    questions=["如何评测？"],
                ),
            ],
            search_queries=["graph neural networks methods evaluation"],
            expected_subdirections=["消息传递", "图表示学习", "节点分类"],
        )
        result = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            [
                PaperCandidate(
                    paper_id="relevant-gnn-cn",
                    title="图神经网络中的消息传递与节点分类方法",
                    abstract="研究图表示学习模型的训练和评测。",
                    year=2025,
                    url="https://example.org/relevant-gnn-cn",
                    source="test",
                ),
                PaperCandidate(
                    paper_id="generic-ai-cn",
                    title="大型语言模型训练方法与评测综述",
                    abstract="讨论人工智能模型的训练、推理和基准评测。",
                    year=2026,
                    url="https://example.org/generic-ai-cn",
                    source="test",
                ),
            ],
            plan,
            limit=2,
        )

        self.assertEqual(
            [paper.paper_id for paper in result.papers],
            ["relevant-gnn-cn"],
        )
        self.assertEqual(result.stats.low_relevance_filtered_count, 1)

    def test_multilingual_lexical_ranking_keeps_relevant_chinese_paper(self) -> None:
        result = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            [
                PaperCandidate(
                    paper_id="relevant-cn",
                    title="面向外部知识问答的检索增强生成方法",
                    abstract="研究检索质量、重排序与语言模型事实可靠性。",
                    year=2025,
                    url="https://example.org/relevant-cn",
                    source="test",
                ),
                PaperCandidate(
                    paper_id="unrelated-cn",
                    title="明清时期江南园林建筑艺术研究",
                    abstract="讨论传统建筑空间布局与审美风格。",
                    year=2026,
                    url="https://example.org/unrelated-cn",
                    source="test",
                ),
            ],
            make_plan(),
            limit=2,
        )

        self.assertEqual([paper.paper_id for paper in result.papers], ["relevant-cn"])
        self.assertEqual(result.stats.vectorizer_backend, "multilingual_tfidf")
        self.assertEqual(result.stats.low_relevance_filtered_count, 1)

    def test_all_unrelated_lexical_candidates_are_rejected(self) -> None:
        result = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            [
                PaperCandidate(
                    paper_id="unrelated-cn",
                    title="明清时期江南园林建筑艺术研究",
                    abstract="讨论传统建筑空间布局与审美风格。",
                    year=2026,
                    url="https://example.org/unrelated-cn",
                    source="test",
                ),
                PaperCandidate(
                    paper_id="unrelated-en",
                    title="Marine Sediment Transport in Coastal Waters",
                    abstract="A study of coastal geology and ocean currents.",
                    year=2025,
                    url="https://example.org/unrelated-en",
                    source="test",
                ),
            ],
            make_plan(),
            limit=2,
        )

        self.assertEqual(result.papers, [])
        self.assertEqual(result.stats.low_relevance_filtered_count, 2)

    def test_canonical_paper_is_core_and_keeps_domain_role(self) -> None:
        result = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            [
                PaperCandidate(
                    paper_id="rag-original",
                    title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                    abstract="retrieval augmented generation method",
                    year=2020,
                    citation_count=100,
                    url="https://example.org/rag-original",
                    source="test",
                )
            ],
            make_plan(),
            limit=1,
        )

        paper = result.papers[0]
        self.assertTrue(paper.is_canonical)
        self.assertEqual(paper.paper_role, "foundational")
        self.assertEqual(paper.reading_priority, "core")

    def test_canonical_arxiv_id_survives_low_relevance_filter(self) -> None:
        config = DomainOnboardingConfig(ranking_min_relevance_score=0.99)
        result = WeightedPaperRanker(config).rank(
            [
                PaperCandidate(
                    paper_id="semantic-rag",
                    title="Title spelling returned by provider",
                    abstract="retrieval augmented generation",
                    year=2020,
                    url="https://example.org/semantic-rag",
                    source="semantic_scholar",
                    arxiv_id="2005.11401",
                )
            ],
            make_plan(),
            limit=1,
        )

        self.assertEqual([paper.paper_id for paper in result.papers], ["semantic-rag"])
        self.assertTrue(result.papers[0].is_canonical)
        self.assertEqual(result.papers[0].paper_role, "foundational")

    def test_recent_vertical_use_case_is_application_not_frontier(self) -> None:
        result = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            [
                PaperCandidate(
                    paper_id="medical-application",
                    title="Retrieval Augmented Generation for Clinical Medical Imaging",
                    abstract="clinical application and medical imaging case study",
                    year=2026,
                    url="https://example.org/medical-application",
                    source="test",
                )
            ],
            make_plan(),
            limit=1,
        )

        paper = result.papers[0]
        self.assertEqual(paper.paper_role, "application")
        self.assertEqual(paper.reading_priority, "optional")

    def test_rag_context_guard_filters_keyword_noise_without_rag_anchor(self) -> None:
        result = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            [
                PaperCandidate(
                    paper_id="rag-paper",
                    title="A Retrieval-Augmented Generation Evaluation Framework",
                    abstract="RAG retrieval generation evaluation",
                    year=2025,
                    url="https://example.org/rag-paper",
                    source="test",
                ),
                PaperCandidate(
                    paper_id="mission-math",
                    title="Doing the Math of Mission",
                    abstract=None,
                    year=2014,
                    url="https://example.org/mission-math",
                    source="test",
                ),
                PaperCandidate(
                    paper_id="rag-disease",
                    title="RAG Deficiencies: Recent Advances in Disease Pathogenesis",
                    abstract="therapeutic approaches in immunology",
                    year=2024,
                    url="https://example.org/rag-disease",
                    source="test",
                ),
                PaperCandidate(
                    paper_id="rag-doll",
                    title="Evaluation of Rag Doll Making Methods for Visual Arts",
                    abstract="craft and textile education",
                    year=2025,
                    url="https://example.org/rag-doll",
                    source="test",
                ),
            ],
            make_plan(),
            limit=2,
        )

        self.assertEqual([paper.paper_id for paper in result.papers], ["rag-paper"])

    def test_diffusion_context_guard_filters_same_word_wrong_field_papers(self) -> None:
        plan = DomainResearchPlan(
            normalized_domain="generative diffusion models",
            perspectives=[
                ResearchPerspective(
                    name=name, description="generative methods", questions=[]
                )
                for name in ("foundations", "methods", "evaluation")
            ],
            search_queries=["denoising diffusion probabilistic models image generation"],
            expected_subdirections=["DDPM", "latent diffusion", "evaluation"],
        )
        papers = [
            PaperCandidate(
                paper_id=paper_id,
                title=title,
                abstract=abstract,
                year=2024,
                url=f"https://example.org/{paper_id}",
                source="test",
            )
            for paper_id, title, abstract in (
                (
                    "ddpm",
                    "Denoising Diffusion Probabilistic Models",
                    "generative image synthesis with denoising diffusion",
                ),
                (
                    "diffusion-mri",
                    "Robust Sampling of Diffusion MRI Microstructure Models",
                    "magnetic resonance imaging microstructure",
                ),
                (
                    "wiener",
                    "First-passage Times from Wiener Diffusion Models",
                    "decision process and first-passage sampling",
                ),
            )
        ]

        result = WeightedPaperRanker(
            DomainOnboardingConfig(ranking_min_role_coverage=0)
        ).rank(papers, plan, limit=3)

        self.assertEqual([paper.paper_id for paper in result.papers], ["ddpm"])
        self.assertEqual(result.stats.low_relevance_filtered_count, 2)

    def test_mmr_chooses_relevant_novel_paper_over_near_duplicate(self) -> None:
        class FixedVectorizer:
            def vectorize(self, texts: list[str]) -> list[dict[str, float]]:
                self.assert_text_count = len(texts)
                return [
                    {"topic": 1.0},
                    {"topic": 1.0},
                    {"topic": 1.0},
                    {"evaluation": 1.0},
                    {"topic": 1.0},
                    {"topic": 0.99, "duplicate": 0.14},
                    {"topic": 0.8, "evaluation": 0.6},
                ]

        config = DomainOnboardingConfig(
            candidate_paper_limit=3,
            selected_paper_limit=2,
            mmr_lambda=0.4,
            mmr_role_bonus=0.0,
        )
        ranker = WeightedPaperRanker(config, vectorizer=FixedVectorizer())
        papers = [
            PaperCandidate(
                paper_id=paper_id,
                title=title,
                abstract=abstract,
                year=2024,
                url=f"https://example.org/{paper_id}",
                source="test",
            )
            for paper_id, title, abstract in (
                ("primary", "RAG Method", "retrieval augmented generation"),
                ("duplicate", "RAG Method Variant", "retrieval augmented generation"),
                ("evaluation", "RAG Evaluation", "evaluation and factuality"),
            )
        ]

        result = ranker.rank(papers, make_plan(), limit=2)

        self.assertEqual(
            [paper.paper_id for paper in result.papers],
            ["primary", "evaluation"],
        )
        self.assertEqual(set(result.stats.mmr_scores), {"primary", "evaluation"})
        self.assertEqual(
            result.stats.ranking_strategy,
            "unified_explainable_score_then_role_mmr",
        )
        self.assertEqual(
            set(result.stats.per_path_candidate_counts),
            {"path-1", "path-2", "path-3"},
        )
        self.assertTrue(result.papers[0].path_relevance_scores)

    def test_explainable_scores_do_not_depend_on_input_order(self) -> None:
        papers = [
            PaperCandidate(
                paper_id=f"paper-{index}",
                title=title,
                abstract=abstract,
                year=2024,
                url=f"https://example.org/{index}",
                source="test",
            )
            for index, (title, abstract) in enumerate(
                (
                    ("RAG Retrieval", "dense retrieval evidence"),
                    ("RAG Generation", "grounded text generation"),
                    ("RAG Evaluation", "factuality benchmark evaluation"),
                )
            )
        ]
        ranker = WeightedPaperRanker(DomainOnboardingConfig(selected_paper_limit=3))

        forward = {
            paper.paper_id: (paper.final_score, paper.score_breakdown.model_dump())
            for paper in ranker.rank(papers, make_plan(), limit=3).papers
        }
        reverse = {
            paper.paper_id: (paper.final_score, paper.score_breakdown.model_dump())
            for paper in ranker.rank(list(reversed(papers)), make_plan(), limit=3).papers
        }

        self.assertEqual(forward, reverse)

    def test_role_gate_covers_available_required_roles_before_duplicates(self) -> None:
        class FixedVectorizer:
            def vectorize(self, texts: list[str]) -> list[dict[str, float]]:
                return [{"topic": 1.0} for _ in texts]

        config = DomainOnboardingConfig(
            selected_paper_limit=3,
            ranking_min_role_coverage=3,
            ranking_required_roles=["survey", "method", "evaluation"],
            ranking_min_relevance_score=0.0,
        )
        papers = [
            PaperCandidate(
                paper_id=paper_id,
                title=title,
                abstract=abstract,
                year=year,
                citation_count=citations,
                url=f"https://example.org/{paper_id}",
                source="test",
            )
            for paper_id, title, abstract, year, citations in (
                ("survey", "RAG Survey", "review overview", 2023, 20),
                ("method", "RAG Framework", "method framework", 2022, 100),
                ("evaluation", "RAG Benchmark", "evaluation benchmark", 2024, 10),
                ("method-2", "RAG Model", "method model", 2021, 10000),
            )
        ]

        result = WeightedPaperRanker(config, vectorizer=FixedVectorizer()).rank(
            papers, make_plan(), limit=3
        )

        self.assertEqual(set(result.stats.covered_roles), {"survey", "method", "evaluation"})
        self.assertEqual(result.stats.missing_required_roles, [])


if __name__ == "__main__":
    unittest.main()
