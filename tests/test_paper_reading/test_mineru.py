from __future__ import annotations

import unittest

from config.schema import MinerUConfig
from handlers.paper_reading.pipeline.mineru import MinerUClient, reflow_document


class MinerUParserTests(unittest.TestCase):
    def test_blank_configuration_is_disabled(self) -> None:
        self.assertFalse(MinerUClient(MinerUConfig()).configured)

    def test_markdown_is_converted_to_downstream_sections(self) -> None:
        document = reflow_document(
            "# A Reflowed Paper\n\n## Abstract\n\nThis is the abstract.\n\n"
            "## 1 Introduction\n\nFirst paragraph.\n\nSecond paragraph."
        )

        self.assertEqual(document["title"], "A Reflowed Paper")
        self.assertEqual(document["section_extraction_source"], "mineru_markdown")
        self.assertEqual(document["sections"][1]["title"], "1 Introduction")
        self.assertEqual(document["sections"][1]["paragraphs"], ["First paragraph.", "Second paragraph."])

    def test_layout_artifacts_without_assets_are_removed_from_reflow(self) -> None:
        document = reflow_document(
            "# Paper\n\n## 1 Intro\n\nac-\ncomplish tasks.\n\n"
            "Received month dd, yyyy; accepted month dd, yyyy\n\n"
            "E-mail: author@example.com\n\n"
            "![](images/missing.jpg)\n\n"
            "<details> <summary>line</summary>chart coordinates</details>\n\nBody."
        )
        content = document["sections"][0]["content"]

        self.assertIn("accomplish tasks", content)
        self.assertNotIn("Received month", content)
        self.assertNotIn("E-mail", content)
        self.assertNotIn("images/missing.jpg", content)
        self.assertNotIn("chart coordinates", content)


if __name__ == "__main__":
    unittest.main()
