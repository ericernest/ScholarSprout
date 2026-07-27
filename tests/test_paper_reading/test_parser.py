"""论文 PDF 结构解析的回归测试。"""

from __future__ import annotations

import unittest

from handlers.paper_reading.pipeline.parser import PDFParser
from handlers.paper_reading.handler import _load_paper_data


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


class PDFParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = PDFParser()

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


if __name__ == "__main__":
    unittest.main()
