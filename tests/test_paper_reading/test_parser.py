"""论文 PDF 结构解析的回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import fitz

from handlers.paper_reading.pipeline.parser import PDFParser
from handlers.paper_reading.handler import (
    _ensure_paper_figures,
    _load_paper_data,
    _paper_detail_for_response,
)
from handlers.paper_reading.harness.storage import PaperReadingStorage


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
