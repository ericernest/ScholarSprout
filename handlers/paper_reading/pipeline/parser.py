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
            return self._parse_document(doc)
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
            return self._parse_document(doc)
        finally:
            doc.close()

    def _parse_document(self, doc: fitz.Document) -> PaperMetadata:
        """Extract a readable single-column text flow and caption-anchored figures."""
        figures = self._extract_figures(doc)
        exclusions_by_page: dict[int, list[fitz.Rect]] = {}
        for figure in figures:
            if figure.page and len(figure.bbox) == 4:
                exclusions_by_page.setdefault(figure.page, []).append(fitz.Rect(figure.bbox))
        page_texts = [
            self._extract_page_text(
                page,
                excluded_regions=exclusions_by_page.get(page_index + 1, []),
            )
            for page_index, page in enumerate(doc)
        ]
        full_text = "\n\n".join(text for text in page_texts if text)
        sections = self.extract_sections(full_text)
        self._assign_section_pages(sections, page_texts)
        self._assign_figure_sections(figures, sections)

        return PaperMetadata(
            paper_id=str(uuid4()),
            source="upload",
            source_id="",
            title=self.extract_title(full_text),
            authors=self.extract_authors(full_text),
            abstract=self.extract_abstract(full_text),
            sections=sections,
            figures=figures,
            tables=[],
            references=self.extract_references(full_text),
            full_text=full_text,
            parse_status="done",
        )

    @staticmethod
    def _extract_page_text(
        page: fitz.Page,
        *,
        excluded_regions: list[fitz.Rect] | None = None,
    ) -> str:
        """Keep PDF text-block boundaries so reflow does not become one wall of text."""
        block_texts: list[str] = []
        excluded_regions = excluded_regions or []
        caption_pattern = re.compile(
            r"^(?:Figure|Fig(?:ure)?\.?)\s*\d+[A-Za-z]?\s*[:.]",
            re.IGNORECASE,
        )
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            block_rect = fitz.Rect(block.get("bbox", (0, 0, 0, 0)))
            block_center = fitz.Point(
                (block_rect.x0 + block_rect.x1) / 2,
                (block_rect.y0 + block_rect.y1) / 2,
            )
            if any(
                region.contains(block_rect.tl)
                or region.contains(block_rect.br)
                or region.contains(block_center)
                for region in excluded_regions
            ):
                continue
            lines: list[str] = []
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                if text:
                    lines.append(text)
            if not lines:
                continue
            block_text = "\n".join(lines)
            if caption_pattern.match(re.sub(r"\s+", " ", block_text).strip()):
                continue
            block_texts.append(block_text)
        return "\n\n".join(block_texts)

    def _extract_figures(self, doc: fitz.Document) -> list[PaperFigure]:
        """Render complete figure regions instead of saving fragmented PDF image objects.

        Scientific figures are commonly composed of many raster snippets, vector paths,
        and text labels. A caption-anchored page crop preserves the complete visual.
        """
        figures: list[PaperFigure] = []
        caption_pattern = re.compile(
            r"^(?:Figure|Fig(?:ure)?\.?)\s*(\d+[A-Za-z]?)\s*[:.]\s*(.+)$",
            re.IGNORECASE,
        )

        for page_index, page in enumerate(doc):
            blocks = page.get_text("dict").get("blocks", [])
            captions: list[tuple[fitz.Rect, str, str]] = []
            for block in blocks:
                if block.get("type") != 0:
                    continue
                text = " ".join(
                    span.get("text", "")
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                )
                text = re.sub(r"\s+", " ", text).strip()
                match = caption_pattern.match(text)
                if match:
                    captions.append((fitz.Rect(block["bbox"]), match.group(1), text))

            captions.sort(key=lambda item: item[0].y0)
            previous_caption_bottom = page.rect.y0
            for caption_rect, figure_number, caption in captions:
                clip = self._figure_clip(
                    page=page,
                    blocks=blocks,
                    caption_rect=caption_rect,
                    lower_limit=previous_caption_bottom,
                )
                previous_caption_bottom = caption_rect.y1
                if clip.is_empty or clip.width < 20 or clip.height < 20:
                    continue

                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(2, 2),
                    colorspace=fitz.csRGB,
                    alpha=False,
                    clip=clip,
                )
                safe_number = re.sub(r"[^0-9A-Za-z_-]", "-", figure_number).lower()
                caption_lower = caption.lower()
                if any(word in caption_lower for word in ("overview", "framework", "architecture")):
                    figure_type = "architecture"
                elif any(word in caption_lower for word in ("performance", "cost", "analysis", "comparison", "results")):
                    figure_type = "chart"
                else:
                    figure_type = "other"
                figures.append(PaperFigure(
                    figure_id=f"fig:{safe_number}",
                    caption=caption,
                    figure_type=figure_type,
                    page=page_index + 1,
                    asset_name=f"figure-{safe_number}-p{page_index + 1}.png",
                    bbox=[round(value, 2) for value in (clip.x0, clip.y0, clip.x1, clip.y1)],
                    width=pixmap.width,
                    height=pixmap.height,
                    image_data=pixmap.tobytes("png"),
                ))

        return figures

    @staticmethod
    def _figure_clip(
        *,
        page: fitz.Page,
        blocks: list[dict[str, Any]],
        caption_rect: fitz.Rect,
        lower_limit: float,
    ) -> fitz.Rect:
        image_rects = [
            fitz.Rect(block["bbox"])
            for block in blocks
            if block.get("type") == 1
            and block.get("bbox")
            and block["bbox"][1] >= lower_limit - 2
            and block["bbox"][3] <= caption_rect.y0 + 2
        ]
        drawing_rects: list[fitz.Rect] = []
        for drawing in page.get_drawings():
            rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
            if rect.y0 >= lower_limit - 2 and rect.y1 <= caption_rect.y0 + 2:
                drawing_rects.append(rect)

        visual_rects = image_rects + drawing_rects
        if visual_rects:
            if image_rects:
                visual_rects.sort(key=lambda rect: rect.y1, reverse=True)
                chosen = [visual_rects[0]]
                top = visual_rects[0].y0
                for rect in visual_rects[1:]:
                    if rect.y1 >= top - 24:
                        chosen.append(rect)
                        top = min(top, rect.y0)
            else:
                # Vector scientific diagrams are often split into distant panels.
                # The neighboring captions already provide safe vertical bounds.
                chosen = drawing_rects
            union = fitz.Rect(chosen[0])
            for rect in chosen[1:]:
                union |= rect
            x0 = min(union.x0, caption_rect.x0) - 7
            x1 = max(union.x1, caption_rect.x1) + 7
            y0 = union.y0 - 7
        else:
            x0 = caption_rect.x0 - 7
            x1 = caption_rect.x1 + 7
            y0 = caption_rect.y0 - min(page.rect.height * 0.38, 300)

        return fitz.Rect(
            max(page.rect.x0, x0),
            max(lower_limit, page.rect.y0, y0),
            min(page.rect.x1, x1),
            min(page.rect.y1, caption_rect.y0 - 2),
        )

    @staticmethod
    def _assign_section_pages(sections: list[PaperSection], page_texts: list[str]) -> None:
        normalized_pages = [re.sub(r"\s+", " ", text).lower() for text in page_texts]
        page_cursor = 0
        previous_page = 1
        for section in sections:
            title = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", section.title).strip().lower()
            title = re.sub(r"^[a-z]\.\s*", "", title).strip()
            found_page: int | None = None
            for page_index in range(page_cursor, len(normalized_pages)):
                if title and title in normalized_pages[page_index]:
                    found_page = page_index + 1
                    page_cursor = page_index
                    break
            section.start_page = found_page or previous_page
            previous_page = section.start_page

        for index, section in enumerate(sections):
            if index + 1 < len(sections):
                next_page = sections[index + 1].start_page or section.start_page or 1
                section.end_page = max(section.start_page or 1, next_page)
            else:
                section.end_page = len(page_texts)

    @staticmethod
    def _assign_figure_sections(
        figures: list[PaperFigure],
        sections: list[PaperSection],
    ) -> None:
        for figure in figures:
            page_candidates = [
                section
                for section in sections
                if section.start_page is not None
                and section.end_page is not None
                and figure.page is not None
                and section.start_page <= figure.page <= section.end_page
            ]
            caption_words = set(re.findall(r"[a-z]{4,}", figure.caption.lower()))
            scored = [
                (
                    len(
                        caption_words
                        & set(re.findall(r"[a-z]{4,}", section.title.lower()))
                    ),
                    -(section.level or 1),
                    section,
                )
                for section in page_candidates
            ]
            if scored and max(score for score, _, _ in scored) > 0:
                figure.section_id = max(scored, key=lambda item: (item[0], item[1]))[2].section_id
                continue

            figure_number = figure.figure_id.partition(":")[2]
            reference_pattern = re.compile(
                rf"(?:Figure|Fig(?:ure)?\.?)\s*{re.escape(figure_number)}\b(?!\s*:)",
                re.IGNORECASE,
            )
            referenced_by = [
                section
                for section in sections
                if reference_pattern.search(section.content)
            ]
            if referenced_by:
                figure.section_id = referenced_by[0].section_id
                continue
            matching = [
                section for section in sections
                if section.start_page is not None
                and figure.page is not None
                and section.start_page <= figure.page
            ]
            if matching:
                figure.section_id = matching[-1].section_id

    # ── 章节提取 ──

    def extract_sections(self, text: str) -> list[PaperSection]:
        """识别章节结构，同时排除页码、公式编号、年份和图表坐标。

        PDF 文本经常把章节编号与标题拆成两行，例如 ``4.1`` 下一行才是
        ``Coarse-grained Memory Retrieval``。旧实现会把任何数字开头的行都
        当作标题，导致公式和参考文献被误切成章节。这里同时校验标题形态与
        顶层章节的连续性。
        """
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        headings: list[tuple[int, int, str, str, int]] = []
        seen_ids: set[str] = set()
        current_top = 0
        references_seen = False
        next_appendix_letter = "A"

        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue

            special = re.fullmatch(
                r"(Abstract|摘要|References|Bibliography|参考文献)",
                line,
                re.IGNORECASE,
            )
            if special:
                name = special.group(1)
                normalized = name.lower()
                section_id = "sec:abstract" if normalized in {"abstract", "摘要"} else "sec:references"
                if section_id not in seen_ids:
                    title = "Abstract" if section_id == "sec:abstract" else "References"
                    headings.append((idx, idx + 1, section_id, title, 1))
                    seen_ids.add(section_id)
                    if section_id == "sec:references":
                        references_seen = True
                continue

            number = ""
            title = ""
            content_start = idx + 1
            appendix = re.fullmatch(r"([A-Z])\.?", line)
            if references_seen and appendix and appendix.group(1) == next_appendix_letter:
                next_index = self._next_nonempty_line(lines, idx + 1)
                if next_index is not None:
                    appendix_title = lines[next_index].strip()
                    if self._looks_like_heading_title(appendix_title):
                        letter = appendix.group(1)
                        section_id = f"sec:appendix:{letter.lower()}"
                        headings.append((
                            idx,
                            next_index + 1,
                            section_id,
                            f"{letter}. {appendix_title}",
                            1,
                        ))
                        seen_ids.add(section_id)
                        next_appendix_letter = chr(ord(letter) + 1)
                        continue

            inline = re.fullmatch(r"(\d+(?:\.\d+)*)\.?\s+(.+)", line)
            if inline:
                number, title = inline.group(1), inline.group(2).strip()
            else:
                number_only = re.fullmatch(r"(\d+(?:\.\d+)*)\.?", line)
                if not number_only:
                    continue
                number = number_only.group(1)
                next_index = self._next_nonempty_line(lines, idx + 1)
                if next_index is None:
                    continue
                title = lines[next_index].strip()
                content_start = next_index + 1

            if not self._looks_like_heading_title(title):
                continue

            parts = [int(part) for part in number.split(".")]
            top = parts[0]
            section_id = f"sec:{number}"
            if section_id in seen_ids:
                continue
            if len(parts) == 1:
                if current_top == 0 and top != 1:
                    continue
                if current_top and top != current_top + 1:
                    continue
                current_top = top
            elif current_top == 0 or top != current_top:
                continue

            headings.append((
                idx,
                content_start,
                section_id,
                f"{number}. {title}",
                min(len(parts), 6),
            ))
            seen_ids.add(section_id)

        if not headings:
            paragraphs = self._lines_to_paragraphs(lines)
            return [PaperSection(
                section_id="sec:full",
                title="Full Text",
                level=1,
                content="\n\n".join(paragraphs)[:10000],
                paragraphs=paragraphs,
            )]

        sections: list[PaperSection] = []
        for index, (heading_line, content_start, section_id, title, level) in enumerate(headings):
            end_line = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
            paragraphs = self._lines_to_paragraphs(lines[content_start:end_line])
            content = "\n\n".join(paragraphs)
            sections.append(PaperSection(
                section_id=section_id,
                title=title,
                level=level,
                content=content,
                paragraphs=paragraphs,
            ))
        return sections

    @staticmethod
    def _next_nonempty_line(lines: list[str], start: int) -> int | None:
        for index in range(start, min(start + 4, len(lines))):
            if lines[index].strip():
                return index
        return None

    @staticmethod
    def _looks_like_heading_title(title: str) -> bool:
        value = re.sub(r"\s+", " ", title).strip()
        if not 2 <= len(value) <= 100:
            return False
        if value.lower() in {
            "query",
            "status",
            "insight",
            "token cost",
            "performance",
            "insights graph",
            "tasks graph",
        }:
            return False
        if not re.match(r"^[A-Za-z一-鿿]", value):
            return False
        if value.endswith((".", ",", ";", ":", "?", "!")):
            return False
        if any(symbol in value for symbol in ("=", "×", "%", "[", "]", "{", "}")):
            return False
        words = value.split()
        if len(words) > 12:
            return False
        alpha_ratio = sum(character.isalpha() for character in value) / max(len(value), 1)
        return alpha_ratio >= 0.58

    @staticmethod
    def _lines_to_paragraphs(lines: list[str]) -> list[str]:
        paragraphs: list[str] = []
        buffer = ""

        def flush() -> None:
            nonlocal buffer
            cleaned = re.sub(r"\s+", " ", buffer).strip()
            if cleaned:
                paragraphs.append(cleaned)
            buffer = ""

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                flush()
                continue
            if re.fullmatch(r"\d{1,3}", line):
                continue
            if re.match(r"^arXiv:\S+\s+\[", line) or line == "Preprint.":
                continue

            starts_list = bool(re.match(r"^(?:[•✱❶❷❸❹❺]|\(\d+\)|\d+[.)]\s)", line))
            starts_labeled_paragraph = bool(re.match(r"^[A-Z][A-Za-z -]{2,45}\.\s", line))
            if buffer and (starts_list or starts_labeled_paragraph or (len(buffer) > 650 and buffer.endswith((".", "?", "!")))):
                flush()

            if buffer.endswith("-") and line[:1].islower():
                buffer = buffer[:-1] + line
            elif buffer:
                buffer += " " + line
            else:
                buffer = line

        flush()
        return paragraphs

    @classmethod
    def sections_need_repair(cls, sections: list[dict[str, Any]] | None) -> bool:
        """判断旧存储中的章节是否来自过宽的数字标题规则。"""
        if not sections:
            return True
        titles = [str(section.get("title", "")).strip() for section in sections]
        suspicious = sum(
            not cls._looks_like_heading_title(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", title))
            for title in titles
        )
        has_expected_section = any(
            re.search(r"\b(?:abstract|introduction|method|experiment|conclusion|reference)\b", title, re.IGNORECASE)
            for title in titles
        )
        return suspicious > 0 or not has_expected_section

    # ── 标题提取 ──

    def extract_title(self, text: str) -> str:
        """提取论文标题。

        启发式: 通常在前 500 字符内，且是第一个较长的非空行。
        对于通过 arXiv API 获取的论文，优先使用 API 返回的标题。
        """
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        candidates: list[str] = []
        for line in lines[:20]:
            if line.lower() in {"abstract", "摘要"}:
                break
            if line.startswith(("arXiv", "http", "DOI")):
                continue
            if candidates and (
                "," in line
                or re.search(r"[∗†]|\b(?:University|Institute|Equal Contribution|Corresponding author)\b", line)
            ):
                break
            if 5 < len(line) < 220:
                candidates.append(line)
            if len(" ".join(candidates)) >= 20 and candidates[-1].endswith((".", "?", "!")):
                break
        title = " ".join(candidates[:3]).strip()
        return title[:300] if title else (lines[0][:200] if lines else "Untitled")

    # ── 作者提取 ──

    def extract_authors(self, text: str) -> list[Author]:
        """从文本中提取作者信息。

        启发式: 标题后的几行中，寻找包含逗号分隔或 "and" 连接的作者行。
        """
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        for line in lines[1:18]:
            if not ("," in line or re.search(r"\s+and\s+|\s+&\s+", line)):
                continue
            if "@" in line or re.search(r"\b(?:University|Institute|Contribution|author)\b", line, re.IGNORECASE):
                continue
            cleaned = re.sub(r"[∗†*]?\d+[∗†*]?", "", line)
            cleaned = re.sub(r"[∗†*]", "", cleaned)
            if not re.fullmatch(r"[A-Za-z一-鿿\s,，;；.\-']{10,300}", cleaned):
                continue
            names = re.split(r"\s*[,，;；]\s*|\s+and\s+|\s+&\s+", cleaned)
            authors = [Author(name=name.strip()) for name in names if 2 < len(name.strip()) < 80]
            if len(authors) >= 2:
                return authors
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
