from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from handlers.paper_reading.handler import (
    _build_quick_paper_payload,
    _preserve_imported_paper_metadata,
)
from storage.paper_reading import PaperReadingStorage


def sample_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Abstract\nThis paper presents a reliable metadata preservation path "
        "for locally imported research papers and their reading workspaces.",
    )
    document.set_metadata({"title": "Embedded PDF title"})
    payload = document.tobytes()
    document.close()
    return payload


class PaperImportMetadataTests(unittest.TestCase):
    def test_domain_metadata_is_available_before_background_parse(self) -> None:
        payload = _build_quick_paper_payload(
            paper_id="paper-1",
            pdf_bytes=sample_pdf_bytes(),
            pdf_url="https://arxiv.org/pdf/2501.12345.pdf",
            metadata={
                "title": "Trusted domain title",
                "authors": ["Ada Researcher", "Lin Scientist"],
                "abstract": "The abstract supplied by domain onboarding.",
                "year": 2025,
                "source_url": "https://arxiv.org/abs/2501.12345",
            },
        )

        self.assertEqual(payload["title"], "Trusted domain title")
        self.assertEqual(payload["authors"], ["Ada Researcher", "Lin Scientist"])
        self.assertEqual(
            payload["abstract"], "The abstract supplied by domain onboarding."
        )
        self.assertEqual(payload["url"], "https://arxiv.org/abs/2501.12345")

    def test_empty_parser_fields_do_not_erase_imported_abstract(self) -> None:
        merged = _preserve_imported_paper_metadata(
            {"title": "Parsed title", "authors": [], "abstract": ""},
            {
                "title": "Imported title",
                "authors": ["Existing Author"],
                "abstract": "Existing abstract",
            },
        )

        self.assertEqual(merged["title"], "Parsed title")
        self.assertEqual(merged["authors"], ["Existing Author"])
        self.assertEqual(merged["abstract"], "Existing abstract")

    def test_arxiv_paper_ids_are_safe_for_windows_pdf_storage(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory) / "paper_reading")
            saved = storage.save_upload("arxiv:2501.12345", b"%PDF-test")

            self.assertTrue(saved.is_file())
            self.assertEqual(storage.get_upload_path("arxiv:2501.12345"), saved)
            self.assertNotIn(":", saved.name)


if __name__ == "__main__":
    unittest.main()
