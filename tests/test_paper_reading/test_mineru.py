from __future__ import annotations

import base64
import io
import json
import unittest
import zipfile

import httpx

from config.schema import MinerUConfig
from handlers.paper_reading.pipeline.mineru import (
    MinerUClient,
    image_url_map,
    parse_mineru_response,
    reflow_document,
)


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

    def test_json_response_keeps_structured_artifacts_and_decodes_images(self) -> None:
        image_bytes = b"\x89PNG\r\n\x1a\nmineru-image"
        content_list = [
            {"type": "title", "text": "Paper title", "text_level": 1},
            {"type": "text", "text": "A sufficiently long structured paragraph for downstream reflow and testing."},
        ]
        response = httpx.Response(
            200,
            json={
                "results": {
                    "paper": {
                        "md_content": "# Paper title\n\n" + "Markdown body. " * 8,
                        "content_list": json.dumps(content_list),
                        "middle_json": json.dumps({"pages": [{"page_id": 0}]}),
                        "images": {"images/figure-1.png": base64.b64encode(image_bytes).decode("ascii")},
                    }
                }
            },
        )

        result = parse_mineru_response(response)

        self.assertEqual(result.response_format, "json")
        self.assertEqual(result.content_list, content_list)
        self.assertEqual(result.middle_json, {"pages": [{"page_id": 0}]})
        self.assertEqual(result.images[0].data, image_bytes)
        self.assertTrue(result.images[0].asset_name.startswith("mineru-"))

    def test_zip_response_extracts_document_bundle_without_writing_archive_paths(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("paper.md", "# ZIP paper\n\n" + "Parsed body. " * 8)
            archive.writestr(
                "paper_content_list.json",
                json.dumps([{"type": "text", "text": "Structured ZIP content."}]),
            )
            archive.writestr("paper_middle.json", json.dumps({"pages": []}))
            archive.writestr("images/chart.jpg", b"jpeg-bytes")
            archive.writestr("../images/ignored.png", b"not-safe")
        response = httpx.Response(
            200,
            content=buffer.getvalue(),
            headers={"content-type": "application/zip"},
        )

        result = parse_mineru_response(response)

        self.assertEqual(result.response_format, "zip")
        self.assertIn("ZIP paper", result.markdown)
        self.assertEqual(result.middle_json, {"pages": []})
        self.assertEqual([asset.source_path for asset in result.images], ["images/chart.jpg"])

    def test_structured_content_is_preferred_and_only_persisted_images_are_rendered(self) -> None:
        response = httpx.Response(
            200,
            json={
                "md_content": "# Raw fallback\n\n" + "This fallback should not be used. " * 6,
                "content_list": [
                    {"type": "header", "text": "Conference header"},
                    {"type": "title", "text": "结构化论文", "text_level": "not-a-number"},
                    {"type": "text", "text": "这是结构化正文，长度足够用于优先生成重排内容，并保留必要的论文信息。" * 3},
                    {"type": "image", "img_path": "images/figure.png", "image_caption": ["方法框架"]},
                    {"type": "table", "table_caption": ["结果"], "table_body": [["方法", "指标"], ["本文", "0.9"]]},
                ],
                "images": {"images/figure.png": base64.b64encode(b"image").decode("ascii")},
            },
        )
        result = parse_mineru_response(response)
        urls = image_url_map("paper-1", result.images)

        document = reflow_document(result.markdown, content_list=result.content_list, image_urls=urls)
        full_text = document["full_text"]

        self.assertIn("结构化论文", full_text)
        self.assertNotIn("Raw fallback", full_text)
        self.assertNotIn("Conference header", full_text)
        self.assertIn("/paper_reading/figures/paper-1/mineru-", full_text)
        self.assertIn("| 方法 | 指标 |", full_text)

    def test_conservative_dehyphenation_keeps_compound_words(self) -> None:
        document = reflow_document(
            "# Paper\n\n## Method\n\nWe ac-\ncomplish the task with a state-\nof-the-art model."
        )

        content = document["sections"][0]["content"]
        self.assertIn("accomplish", content)
        self.assertIn("state-of-the-art", content)


if __name__ == "__main__":
    unittest.main()
