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


if __name__ == "__main__":
    unittest.main()
