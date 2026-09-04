"""Repository-wide branding invariants."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRECTORIES = {".git", ".pytest_cache", ".venv", "__pycache__", "node_modules"}
LEGACY_PATTERNS = (
    re.compile("novice" + r"[\s._-]*" + "synapse", re.IGNORECASE),
    re.compile("see" + r"[\s._-]*" + "further", re.IGNORECASE),
    re.compile("synapse" + r"\s+" + "copilot", re.IGNORECASE),
    re.compile("research" + r"\s+" + "ciopilot", re.IGNORECASE),
    re.compile("研" + "见"),
)


def _repository_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in SKIPPED_DIRECTORIES for part in path.parts)
    ]


class BrandingTests(unittest.TestCase):
    def test_repository_has_no_retired_brand_names(self) -> None:
        violations: list[str] = []
        for path in _repository_files():
            relative_path = path.relative_to(ROOT).as_posix()
            if any(pattern.search(relative_path) for pattern in LEGACY_PATTERNS):
                violations.append(f"path: {relative_path}")
                continue

            raw = path.read_bytes()
            if b"\x00" in raw:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for pattern in LEGACY_PATTERNS:
                if pattern.search(text):
                    violations.append(f"content: {relative_path}")
                    break

        self.assertEqual([], violations)

    def test_paper_reading_uses_reading_pilot(self) -> None:
        reading_root = ROOT / "gateway" / "static" / "paper-reading"
        content = "\n".join(
            path.read_text(encoding="utf-8")
            for path in reading_root.rglob("*")
            if path.is_file()
        )
        self.assertGreaterEqual(content.count("Reading Pilot"), 4)


if __name__ == "__main__":
    unittest.main()
