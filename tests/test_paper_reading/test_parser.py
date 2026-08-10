"""论文 PDF 结构解析的回归测试。"""

from __future__ import annotations

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import fitz

from handlers.paper_reading.pipeline.parser import PDFParser
from handlers.paper_reading.handler import (
    _build_llm_reading_map,
    _build_reading_map,
    _ensure_paper_figures,
    _load_paper_data,
    _normalize_reading_map,
    _paper_detail_for_response,
    _ensure_fact_sources,
    _survey_text_chunks,
    _validate_survey_reading_map,
)
from handlers.paper_reading.harness.storage import PaperReadingStorage
from handlers.paper_reading.postprocessors.postprocess import postprocess_agent_output


SAMPLE_TEXT = """arXiv:2506.07398v2  [cs.MA]  16 Jun 2025
G-Memory: Tracing Hierarchical Memory for
Multi-Agent Systems
Guibin Zhang∗1, Muxin Fu∗2, Guancheng Wan3, Shuicheng Yan1†
1NUS, 2Tongji University
Abstract
This paper introduces a hierarchical memory architecture for multi-agent systems.

1
Introduction
The introduction spans several wrapped
lines and explains the problem.
2
This is a page footer followed by ordinary body text, not a heading.
2
Related Works
Related work content.
3
Method
Method overview.
3.1
Memory Retrieval
Retrieval details and equation:
4
Query
5.4 × 106 tokens for a mere 4.07% gain.
4
Experiment
Experiment content.
5
Conclusion & Limitation
Conclusion content.
References
[1] A reference entry from 2023.
2023. 2023.
"""


def synthetic_figure_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 60), "Visual Reflow for Scientific Papers", fontsize=18)
    page.insert_text((72, 88), "Alice Example", fontsize=10)
    page.insert_text((72, 120), "Abstract", fontsize=13)
    page.insert_textbox(
        fitz.Rect(72, 138, 540, 190),
        "This paper tests whether complete scientific figures survive PDF reflow.",
        fontsize=10,
    )
    page.draw_rect(fitz.Rect(110, 225, 500, 390), color=(0.1, 0.4, 0.7), fill=(0.9, 0.95, 1))
    page.draw_line((135, 350), (465, 260), color=(0.8, 0.2, 0.2), width=3)
    page.insert_text((230, 310), "Complete diagram", fontsize=16)
    page.insert_textbox(
        fitz.Rect(110, 405, 500, 435),
        "Figure 1: Overview of the complete reflow pipeline.",
        fontsize=10,
    )
    page.insert_text((72, 485), "1", fontsize=12)
    page.insert_text((72, 505), "Introduction", fontsize=12)
    page.insert_textbox(
        fitz.Rect(72, 525, 540, 570),
        "Figure 1 shows the visual pipeline. The surrounding prose remains readable.",
        fontsize=10,
    )
    payload = document.tobytes()
    document.close()
    return payload


def synthetic_outline_pdf() -> bytes:
    document = fitz.open()
    titles = [
        "Outline Driven Paper",
        "Abstract",
        "1 Introduction",
        "2 Methods",
        "2.1 Components",
        "3 Experiments",
        "References",
    ]
    for title in titles:
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), title, fontsize=16)
        page.insert_textbox(
            fitz.Rect(72, 110, 540, 260),
            f"{title}. Body text for this outline section.",
            fontsize=10,
        )
    document.set_toc([
        [1, "Abstract", 2],
        [1, "1 Introduction", 3],
        [1, "2 Methods", 4],
        [2, "2.1 Components", 5],
        [1, "3 Experiments", 6],
        [1, "References", 7],
    ])
    payload = document.tobytes()
    document.close()
    return payload


class PDFParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = PDFParser()

    def test_extract_year_prefers_arxiv_identifier(self) -> None:
        self.assertEqual(
            self.parser.extract_year(
                "A paper that compares methods from 2023.",
                source_hint="https://arxiv.org/pdf/2506.07398.pdf",
            ),
            2025,
        )

    def test_extract_abstract_accepts_inline_em_dash_label(self) -> None:
        text = (
            "Paper title\nAlice Example\n"
            "Abstract—This paper presents a reliable approach for extracting inline abstracts "
            "from scientific PDF text while preserving enough content for a useful paper card.\n"
            "1 Introduction\nThe rest of the paper starts here."
        )

        abstract = self.parser.extract_abstract(text)

        self.assertTrue(abstract.startswith("This paper presents"))
        self.assertNotIn("Introduction", abstract)

    def test_extract_year_uses_pdf_creation_metadata_as_fallback(self) -> None:
        self.assertEqual(
            self.parser.extract_year(
                "Paper title without a publication line",
                document_metadata={"creationDate": "D:20240203112200Z"},
            ),
            2024,
        )

    def test_split_number_and_title_lines_form_sections(self) -> None:
        sections = self.parser.extract_sections(SAMPLE_TEXT)

        self.assertEqual(
            [section.title for section in sections],
            [
                "Abstract",
                "1. Introduction",
                "2. Related Works",
                "3. Method",
                "3.1. Memory Retrieval",
                "4. Experiment",
                "5. Conclusion & Limitation",
                "References",
            ],
        )
        self.assertNotIn("2023. 2023.", [section.title for section in sections])
        self.assertNotIn("4. Query", [section.title for section in sections])

    def test_multiline_title_and_marked_authors_are_normalized(self) -> None:
        self.assertEqual(
            self.parser.extract_title(SAMPLE_TEXT),
            "G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems",
        )
        self.assertEqual(
            [author.name for author in self.parser.extract_authors(SAMPLE_TEXT)],
            ["Guibin Zhang", "Muxin Fu", "Guancheng Wan", "Shuicheng Yan"],
        )

    def test_old_numeric_false_positive_sections_request_repair(self) -> None:
        broken = [
            {"title": "2023. 2023."},
            {"title": "9. figures, 2 tables."},
        ]
        healthy = [
            {"title": "Abstract"},
            {"title": "1. Introduction"},
            {"title": "2. Method"},
        ]

        self.assertTrue(PDFParser.sections_need_repair(broken))
        self.assertFalse(PDFParser.sections_need_repair(healthy))

    def test_lettered_appendices_after_references_keep_outline_hierarchy(self) -> None:
        text = """Abstract
Summary.
1
Introduction
Body.
References
[1] Reference.
A
Experimental Details
Setup details.
B
Additional Results
More results.
"""

        sections = self.parser.extract_sections(text)

        self.assertEqual(
            [section.title for section in sections],
            [
                "Abstract",
                "1. Introduction",
                "References",
                "A. Experimental Details",
                "B. Additional Results",
            ],
        )

    def test_loading_old_paper_repairs_structure_title_and_authors_in_memory(self) -> None:
        stored = {
            "paper_id": "old-paper",
            "title": "G-Memory: Tracing Hierarchical Memory for",
            "authors": [{"name": "Multi-Agent Systems"}],
            "sections": [{"section_id": "sec:2023", "title": "2023. 2023."}],
            "full_text": SAMPLE_TEXT,
        }

        class Storage:
            @staticmethod
            def load_paper(paper_id: str) -> dict:
                return stored

        repaired = _load_paper_data(Storage(), "old-paper")

        self.assertEqual(
            repaired["title"],
            "G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems",
        )
        self.assertEqual(
            [author["name"] for author in repaired["authors"]],
            ["Guibin Zhang", "Muxin Fu", "Guancheng Wan", "Shuicheng Yan"],
        )
        self.assertEqual(repaired["sections"][1]["title"], "1. Introduction")
        self.assertEqual(stored["title"], "G-Memory: Tracing Hierarchical Memory for")

    def test_parse_bytes_extracts_complete_caption_anchored_figure(self) -> None:
        metadata = self.parser.parse_bytes(synthetic_figure_pdf())

        self.assertEqual(metadata.section_extraction_source, "heuristic")
        self.assertEqual(metadata.section_extraction_status, "outline_missing_fallback_heuristic")
        self.assertEqual(len(metadata.figures), 1)
        figure = metadata.figures[0]
        self.assertEqual(figure.figure_id, "fig:1")
        self.assertEqual(figure.section_id, "sec:1")
        self.assertEqual(figure.page, 1)
        self.assertEqual(figure.asset_name, "figure-1-p1.png")
        self.assertTrue(figure.image_data.startswith(b"\x89PNG"))
        self.assertGreater(figure.width or 0, 400)
        self.assertGreater(figure.height or 0, 150)
        self.assertNotIn("image_data", metadata.model_dump()["figures"][0])

    def test_parse_bytes_prefers_pdf_outline_for_sections(self) -> None:
        metadata = self.parser.parse_bytes(synthetic_outline_pdf())

        self.assertEqual(metadata.section_extraction_source, "pdf_outline")
        self.assertEqual(metadata.section_extraction_status, "outline_used")
        self.assertEqual(metadata.outline_entries_count, 6)
        self.assertEqual(
            [section.title for section in metadata.sections],
            [
                "Abstract",
                "1 Introduction",
                "2 Methods",
                "2.1 Components",
                "3 Experiments",
                "References",
            ],
        )
        self.assertEqual(metadata.sections[3].level, 2)
        self.assertEqual(metadata.sections[3].start_page, 5)

    def test_survey_reading_map_has_type_specific_sections_and_cards(self) -> None:
        sections = [
            {
                "section_id": "sec:abstract",
                "title": "Abstract",
                "level": 1,
                "content": "This survey reviews retrieval augmented generation methods.",
                "paragraphs": ["This survey reviews retrieval augmented generation methods."],
                "start_page": 1,
            },
            {
                "section_id": "sec:2",
                "title": "2 Taxonomy of Retrieval-Augmented Methods",
                "level": 1,
                "content": "Methods can be categorized by retriever, generator, and training strategy.",
                "paragraphs": ["Methods can be categorized by retriever, generator, and training strategy."],
                "start_page": 4,
            },
            {
                "section_id": "sec:3",
                "title": "3 Public Datasets and Benchmark Protocols",
                "level": 1,
                "content": "Datasets and benchmarks include question answering corpora and evaluation metrics such as F1.",
                "paragraphs": ["Datasets and benchmarks include question answering corpora and evaluation metrics such as F1."],
                "start_page": 8,
            },
            {
                "section_id": "sec:4",
                "title": "4 Challenges and Future Directions",
                "level": 1,
                "content": "Open challenges include freshness, attribution, and robust evaluation.",
                "paragraphs": ["Open challenges include freshness, attribution, and robust evaluation."],
                "start_page": 12,
            },
        ]
        reading_map = _build_reading_map({
            "title": "A Survey of Retrieval-Augmented Generation",
            "abstract": sections[0]["content"],
            "sections": sections,
        })

        self.assertEqual(reading_map["paper_type"], "survey")
        self.assertEqual(reading_map["map_variant"], "survey")
        self.assertIn("technical_routes", reading_map["survey_map"])
        self.assertTrue(reading_map["prerequisite_card"]["concepts"])
        self.assertTrue(reading_map["section_guides"][1]["cards"])

    def test_memory_survey_reading_map_uses_outline_style_sections(self) -> None:
        sections = [
            {
                "section_id": "sec:1",
                "title": "1 Introduction",
                "level": 1,
                "content": "This survey defines memory for AI agents and explains why long-horizon tasks need memory.",
                "paragraphs": [],
                "start_page": 3,
            },
            {
                "section_id": "sec:3",
                "title": "3 Form: What Carries Memory?",
                "level": 1,
                "content": "Memory can be organized by token-level, parametric, latent, and adaptation forms.",
                "paragraphs": [],
                "start_page": 12,
            },
            {
                "section_id": "sec:4",
                "title": "4 Functions: Why Agents Need Memory?",
                "level": 1,
                "content": "Functional memory includes factual, experiential, and working memory.",
                "paragraphs": [],
                "start_page": 31,
            },
            {
                "section_id": "sec:5",
                "title": "5 Dynamics: How Memory Operates and Evolves?",
                "level": 1,
                "content": "Memory operates through formation, evolution, retrieval, updating, and forgetting.",
                "paragraphs": [],
                "start_page": 46,
            },
            {
                "section_id": "sec:6",
                "title": "6 Resources and Frameworks",
                "level": 1,
                "content": "Benchmarks, datasets, and open-source frameworks are collected for agent memory.",
                "paragraphs": [],
                "start_page": 70,
            },
            {
                "section_id": "sec:7",
                "title": "7 Positions and Frontiers",
                "level": 1,
                "content": "Open challenges include scalability, evaluation, privacy, and long-term reliability.",
                "paragraphs": [],
                "start_page": 85,
            },
        ]
        reading_map = _build_reading_map({
            "title": "Memory in the Age of AI Agents: A Survey",
            "abstract": sections[0]["content"],
            "sections": sections,
        })

        survey = reading_map["survey_map"]
        self.assertEqual(reading_map["paper_type"], "survey")
        self.assertTrue(survey["taxonomy"])
        self.assertTrue(survey["technical_routes"])
        self.assertTrue(survey["representative_methods"])
        self.assertTrue(survey["datasets"])
        self.assertTrue(survey["open_challenges"])

    def test_survey_map_empty_llm_lists_do_not_clear_fallback(self) -> None:
        fallback = {
            "paper_type": "survey",
            "map_variant": "survey",
            "prerequisite_card": {},
            "survey_map": {
                "field_overview": {"field": "Agent Memory", "core_task": "Survey memory systems."},
                "technical_routes": [{"name": "Memory retrieval", "core_idea": "Retrieve useful memories."}],
                "datasets": [{"name": "Agent memory benchmarks", "content": "Benchmarks are listed."}],
            },
            "section_guides": [{"section_id": "sec:1", "title": "1 Introduction", "cards": []}],
        }
        normalized = _normalize_reading_map({
            "paper_type": "survey",
            "map_variant": "survey",
            "survey_map": {
                "field_overview": {"why_now": "Agents need longer context."},
                "technical_routes": [],
                "datasets": [],
            },
            "section_guides": [],
        }, fallback)

        survey = normalized["survey_map"]
        self.assertEqual(survey["field_overview"]["field"], "Agent Memory")
        self.assertEqual(survey["field_overview"]["why_now"], "Agents need longer context.")
        self.assertTrue(survey["technical_routes"])
        self.assertTrue(survey["datasets"])
        self.assertTrue(normalized["section_guides"])

    def test_llm_reading_map_failure_does_not_expose_fallback(self) -> None:
        fallback = {
            "paper_type": "survey",
            "map_variant": "survey",
            "survey_map": {
                "field_overview": {"field": "Agent Memory"},
                "technical_routes": [{"name": "Fallback route"}],
            },
            "section_guides": [{"section_id": "sec:1", "title": "1 Introduction", "cards": [{"title": "Fallback"}]}],
        }
        reading_map = _build_llm_reading_map(
            paper={"title": "Survey", "sections": [{"section_id": "sec:1", "title": "1 Introduction"}]},
            fallback=fallback,
            model=None,
            skill_registry=None,
        )

        self.assertEqual(reading_map["status"], "failed")
        self.assertFalse(reading_map["survey_map"])
        self.assertFalse(reading_map["section_guides"])

    def test_survey_text_chunks_cover_full_long_sections(self) -> None:
        long_text = " ".join(f"Sentence {index} describes a survey fact." for index in range(900))
        chunks = _survey_text_chunks({
            "sections": [
                {
                    "section_id": "sec:taxonomy",
                    "title": "3 A Content-Based Organization",
                    "level": 1,
                    "content": long_text,
                    "start_page": 10,
                    "end_page": 20,
                }
            ]
        }, max_chars=1200)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0]["section_id"], "sec:taxonomy")
        self.assertTrue(all(chunk["text_hash"] for chunk in chunks))
        joined = " ".join(chunk["text"] for chunk in chunks)
        self.assertIn("Sentence 899 describes a survey fact.", joined)

    def test_ensure_fact_sources_does_not_create_recursive_source_refs(self) -> None:
        source = {"section_id": "sec:1", "title": "1 Introduction", "page": 1}
        facts = {
            "taxonomy": [
                {"category": "Memory types", "summary": "A taxonomy item."}
            ],
            "source_sections": [source],
        }

        _ensure_fact_sources(facts, source)
        payload = json.dumps(facts, ensure_ascii=False)

        self.assertIn("Memory types", payload)
        self.assertEqual(facts["taxonomy"][0]["source_sections"][0], source)
        self.assertNotIn("source_sections", facts["source_sections"][0])

    def test_survey_reading_map_validation_rejects_section_title_methods(self) -> None:
        reading_map = {
            "map_variant": "survey",
            "survey_map": {
                "field_overview": {"field": "Agent Memory"},
                "taxonomy": [{"category": "Memory form", "source_sections": [{"section_id": "s1"}]}],
                "technical_routes": [{"name": "Memory retrieval", "source_sections": [{"section_id": "s2"}]}],
                "representative_methods": [{"paper_title": "3 Form: What Carries Memory?", "source_sections": [{"section_id": "s3"}]}],
                "datasets": [{"name": "LongMemEval", "source_sections": [{"section_id": "s4"}]}],
                "open_challenges": [{"challenge": "Evaluation", "source_sections": [{"section_id": "s5"}]}],
            },
            "section_guides": [{"section_id": "s1", "cards": [{"title": "Read"}]}],
        }

        self.assertFalse(_validate_survey_reading_map(reading_map))

    def test_skill_postprocessor_removes_graph_fields_before_frontend(self) -> None:
        outputs = postprocess_agent_output(
            (
                '{"problem_formulation":{"input":"x"},'
                '"dependency_graph":{"nodes":[{"id":"a"}],"edges":[]},'
                '"core_innovation_analysis":{"what":"better retrieval"}}'
            ),
            ["reading.method_analyst"],
        )

        content = outputs[0]["content"]
        self.assertIn("problem_formulation", content)
        self.assertNotIn("dependency_graph", content)

    def test_legacy_paper_backfills_figure_assets_and_image_url(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory))
            storage.save_upload("legacy-paper", synthetic_figure_pdf())
            paper = {
                "paper_id": "legacy-paper",
                "title": "Legacy paper",
                "sections": [{"section_id": "sec:1", "title": "1. Introduction"}],
                "figures": [],
            }
            pipeline = SimpleNamespace(parse_pdf=self.parser.parse)

            repaired = _ensure_paper_figures(
                paper=paper,
                upload_path=storage.get_upload_path("legacy-paper"),
                storage=storage,
                pipeline=pipeline,
            )
            detail = _paper_detail_for_response(repaired)

            self.assertEqual(repaired["figure_extraction_status"], "done")
            self.assertEqual(len(repaired["figures"]), 1)
            asset_name = repaired["figures"][0]["asset_name"]
            self.assertIsNotNone(storage.get_figure_path("legacy-paper", asset_name))
            self.assertEqual(
                detail["figures"][0]["image_url"],
                f"/paper_reading/figures/legacy-paper/{asset_name}",
            )


if __name__ == "__main__":
    unittest.main()
