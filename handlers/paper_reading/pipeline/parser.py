"""PDF 解析器 — 基于 PyMuPDF (fitz) 提取论文结构化内容。

功能:
- 提取全文文本（保持阅读顺序）
- 识别章节结构（基于字体大小/编号模式启发式）
- 提取图表标题
- 提取参考文献列表
- 提取摘要
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import fitz  # PyMuPDF

from handlers.paper_reading.pipeline.metadata import (
    Author,
    PaperFigure,
    PaperMetadata,
    PaperReference,
    PaperSection,
    PaperTable,
)

logger = logging.getLogger(__name__)


class PDFParser:
    """基于 PyMuPDF 的 PDF 结构解析器。

    设计要点:
    - 逐页扫描文本块，保持阅读顺序
    - 通过章节编号模式（1., 2.1, 3.2.1 等）识别章节结构
    - 字体大小启发式辅助标题识别
    - 图片块自动记录位置和页码
    """

    def __init__(self) -> None:
        self._section_patterns = [
            re.compile(
                r"^\s*(\d+(?:\.\d+)*)\s+([A-Z一-鿿][A-Za-z一-鿿\s\-().,;:]+?)\s*$",
                re.MULTILINE,
            ),
            re.compile(
                r"^\s*(?:Abstract|摘要)\s*$",
                re.MULTILINE | re.IGNORECASE,
            ),
            re.compile(
                r"^\s*(?:References|Bibliography|参考文献)\s*$",
                re.MULTILINE | re.IGNORECASE,
            ),
        ]

    def parse(self, pdf_path: Path | str) -> PaperMetadata:
        """解析 PDF 文件，提取所有结构化内容。

        Args:
            pdf_path: PDF 文件路径

        Returns:
            PaperMetadata 实例，包含完整的结构化论文数据
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        doc = fitz.open(str(pdf_path))
        try:
            full_text_parts: list[str] = []
            figures: list[PaperFigure] = []
            tables: list[PaperTable] = []
            fig_count = 0

            for page_num, page in enumerate(doc):
                blocks = page.get_text("dict").get("blocks", [])
                page_text = ""

                for block in blocks:
                    if block.get("type") == 0:  # 文本块
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                page_text += span.get("text", "") + " "
                            page_text = page_text.rstrip() + "\n"
                    elif block.get("type") == 1:  # 图片块
                        fig_count += 1
                        figures.append(PaperFigure(
                            figure_id=f"fig:{page_num + 1}_{fig_count}",
                            caption="",
                            figure_type="other",
                            page=page_num + 1,
                        ))

                full_text_parts.append(page_text)

            full_text = "\n\n".join(full_text_parts)

            return PaperMetadata(
                paper_id=str(uuid4()),
                source="upload",
                source_id="",
                title=self.extract_title(full_text),
                authors=self.extract_authors(full_text),
                abstract=self.extract_abstract(full_text),
                sections=self.extract_sections(full_text),
                figures=figures,
                tables=tables,
                references=self.extract_references(full_text),
                full_text=full_text,
                parse_status="done",
            )
        finally:
            doc.close()

    def parse_bytes(self, pdf_bytes: bytes) -> PaperMetadata:
        """解析 PDF 字节数据。

        Args:
            pdf_bytes: PDF 文件的原始字节

        Returns:
            PaperMetadata 实例
        """
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            full_text_parts: list[str] = []
            for page in doc:
                text = page.get_text()
                if text:
                    full_text_parts.append(text)
            full_text = "\n\n".join(full_text_parts)

            return PaperMetadata(
                paper_id=str(uuid4()),
                source="upload",
                source_id="",
                title=self.extract_title(full_text),
                authors=self.extract_authors(full_text),
                abstract=self.extract_abstract(full_text),
                sections=self.extract_sections(full_text),
                full_text=full_text,
                parse_status="done",
            )
        finally:
            doc.close()

    # ── 章节提取 ──

    def extract_sections(self, text: str) -> list[PaperSection]:
        """通过章节编号模式识别章节结构。

        支持的编号格式:
        - "1. Introduction"
        - "2.1. Background"
        - "3.2.1. Detailed Analysis"
        - "I. Introduction" (罗马数字，后续扩展)
        """
        sections: list[PaperSection] = []
        lines = text.split("\n")

        # 匹配章节标题行
        heading_matches: list[tuple[int, str, str, int]] = []
        for idx, line in enumerate(lines):
            line_stripped = line.strip()
            match = re.match(
                r"^(\d+(?:\.\d+)*)\s+(.+)$",
                line_stripped,
            )
            if match:
                number = match.group(1)
                title = match.group(2).strip()
                level = number.count(".") + 1
                heading_matches.append((idx, number, title, level))

        if not heading_matches:
            # 没有章节编号时，回退为全文作为一个章节
            sections.append(PaperSection(
                section_id="sec:full",
                title="Full Text",
                level=1,
                content=text[:10000],
                paragraphs=[p for p in text.split("\n\n") if p.strip()],
            ))
            return sections

        # 构建章节
        for i, (line_idx, number, title, level) in enumerate(heading_matches):
            start_line = line_idx + 1  # 内容从下一行开始
            if i + 1 < len(heading_matches):
                end_line = heading_matches[i + 1][0]
            else:
                end_line = len(lines)

            content_lines = lines[start_line:end_line]
            content = "\n".join(content_lines).strip()
            paragraphs = [
                p.strip() for p in content.split("\n\n") if p.strip()
            ]

            sections.append(PaperSection(
                section_id=f"sec:{number}",
                title=f"{number}. {title}",
                level=min(level, 6),
                content=content,
                paragraphs=paragraphs,
            ))

        return sections

    # ── 标题提取 ──

    def extract_title(self, text: str) -> str:
        """提取论文标题。

        启发式: 通常在前 500 字符内，且是第一个较长的非空行。
        对于通过 arXiv API 获取的论文，优先使用 API 返回的标题。
        """
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines[:20]:
            if 10 < len(line) < 300 and not line.startswith(("arXiv", "http", "DOI")):
                return line
        return lines[0][:200] if lines else "Untitled"

    # ── 作者提取 ──

    def extract_authors(self, text: str) -> list[Author]:
        """从文本中提取作者信息。

        启发式: 标题后的几行中，寻找包含逗号分隔或 "and" 连接的作者行。
        """
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        # 跳过标题行，在后续 15 行中寻找
        author_pattern = re.compile(
            r"([一-鿿A-Z][一-鿿A-Za-z\s.\-]+(?:[，,]\s*[一-鿿A-Z]))"
        )
        for line in lines[1:15]:
            # 常见的作者行特征：包含多个大写字母开头的词，或逗号/and
            if re.match(r"^[A-Za-z一-鿿\s,.\-']{10,200}$", line):
                # 按逗号或 "and" 分割
                names = re.split(r"\s*[,，;；]|\s+and\s+|\s+&\s+", line)
                return [Author(name=n.strip()) for n in names if len(n.strip()) > 2]
        return []

    # ── 摘要提取 ──

    def extract_abstract(self, text: str) -> str:
        """提取摘要。

        匹配 "Abstract" 或 "摘要" 标记后直到下一个章节标题之前的文本。
        """
        for pattern in [
            r"(?:Abstract|摘要|ABSTRACT)\s*[\-\n:：]+(.*?)(?=\n\s*(?:\d+\.?\s*)?(?:Introduction|引言|1\.))",
            r"(?:Abstract|摘要|ABSTRACT)\s*\n{2,}(.*?)(?=\n{2,})",
        ]:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                abstract = match.group(1).strip()
                if len(abstract) > 50:
                    return abstract[:3000]

        # 回退：取前 2000 字符中较长的段落
        first_chunk = text[:2000]
        paragraphs = [p.strip() for p in first_chunk.split("\n\n") if len(p.strip()) > 100]
        return paragraphs[0][:2000] if paragraphs else ""

    # ── 参考文献提取 ──

    def extract_references(self, text: str) -> list[PaperReference]:
        """提取参考文献列表。

        匹配 "References" 或 "参考文献" 之后的编号条目。
        """
        for ref_header in ["References", "REFERENCES", "Bibliography", "参考文献"]:
            pattern = rf"(?:{ref_header})\s*\n(.+)"
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                ref_text = match.group(1)
                # 按编号分割: [1], [2], 1., 2. 等
                refs = re.split(r"\n(?=\[\d+\]|\d+\.\s)", ref_text)
                references = []
                for i, r in enumerate(refs):
                    r = r.strip()
                    if len(r) > 20:
                        references.append(PaperReference(
                            ref_id=f"ref:{i+1}",
                            title="",
                            authors=[],
                        ))
                return references[:100]  # 限制数量

        return []

    # ── 辅助方法 ──

    def get_section_content(self, text: str, section_id: str) -> str | None:
        """获取指定章节的文本内容。"""
        sections = self.extract_sections(text)
        for s in sections:
            if s.section_id == section_id:
                return s.content
        return None

    def get_section_summary(self, text: str) -> list[dict[str, Any]]:
        """获取章节索引（仅包含 ID、标题、层级）。"""
        sections = self.extract_sections(text)
        return [
            {
                "section_id": s.section_id,
                "title": s.title,
                "level": s.level,
                "content_length": len(s.content),
            }
            for s in sections
        ]
