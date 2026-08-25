"""Optional MinerU parsing adapter used alongside the local PDF parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from config.schema import MinerUConfig


@dataclass(slots=True)
class MinerUParseResult:
    markdown: str


class MinerUClient:
    def __init__(self, config: MinerUConfig) -> None:
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(self.config.base_url and self.config.api_key.strip())

    def parse_pdf(self, pdf_bytes: bytes) -> MinerUParseResult:
        if not self.configured:
            raise RuntimeError("MinerU is not configured")
        with httpx.Client(trust_env=False, timeout=self.config.timeout) as client:
            response = client.post(
                str(self.config.base_url),
                headers={"Authorization": f"Bearer {self.config.api_key.strip()}"},
                files={"files": ("paper.pdf", pdf_bytes, "application/pdf")},
                data={"return_md": "true", "response_format_zip": "false"},
            )
            response.raise_for_status()
            markdown = _extract_markdown(response)
        if len(markdown.strip()) < 80:
            raise RuntimeError("MinerU returned no usable Markdown")
        return MinerUParseResult(markdown=markdown.strip())


def _extract_markdown(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        return response.text
    payload = response.json()
    preferred = ("markdown", "md", "content", "text", "result", "data")

    def visit(value: Any) -> str:
        if isinstance(value, str):
            return value if len(value.strip()) >= 80 else ""
        if isinstance(value, dict):
            for key in preferred:
                if key in value:
                    found = visit(value[key])
                    if found:
                        return found
            for nested in value.values():
                found = visit(nested)
                if found:
                    return found
        if isinstance(value, list):
            for nested in value:
                found = visit(nested)
                if found:
                    return found
        return ""

    return visit(payload)


def reflow_document(markdown: str) -> dict[str, Any]:
    """Convert MinerU Markdown into the section contract used downstream."""
    lines = markdown.replace("\r\n", "\n").splitlines()
    headings: list[tuple[int, str, list[str]]] = []
    preamble: list[str] = []
    current: tuple[int, str, list[str]] | None = None
    for raw in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw)
        if match:
            current = (len(match.group(1)), match.group(2).strip(), [])
            headings.append(current)
            continue
        (current[2] if current else preamble).append(raw)

    title = ""
    if headings and headings[0][0] == 1:
        title = headings[0][1]
        headings = headings[1:]
    if not title:
        title = next((line.strip() for line in preamble if len(line.strip()) > 5), "")

    sections: list[dict[str, Any]] = []
    for index, (level, section_title, body_lines) in enumerate(headings, start=1):
        paragraphs = [
            re.sub(r"\s+", " ", block).strip()
            for block in re.split(r"\n\s*\n", "\n".join(body_lines))
            if re.sub(r"\s+", " ", block).strip()
        ]
        sections.append({
            "section_id": f"mineru:{index}",
            "title": section_title,
            "level": level,
            "content": "\n\n".join(paragraphs),
            "paragraphs": paragraphs,
            "start_page": None,
            "end_page": None,
        })
    abstract = ""
    for section in sections:
        if section["title"].strip().lower() in {"abstract", "摘要"}:
            abstract = section["content"][:3000]
            break
    return {
        "title": title[:300],
        "abstract": abstract,
        "sections": sections,
        "full_text": markdown,
        "section_extraction_source": "mineru_markdown",
        "section_extraction_status": "mineru_used",
        "section_extraction_message": "已使用 MinerU AI 论文重排结果。",
        "outline_entries_count": len(sections),
    }
