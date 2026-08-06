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
    PaperLayoutElement,
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
        page_texts = [
            self._extract_page_text(page)
            for page in doc
        ]
        full_text = "\n\n".join(text for text in page_texts if text)
        sections = self.extract_sections(full_text)
        self._assign_section_pages(sections, page_texts)
        self._assign_figure_sections(figures, sections)
        tables, table_elements = self._extract_tables(doc, sections)
        layout_elements = self._extract_layout_elements(
            doc=doc,
            sections=sections,
            figures=figures,
            table_elements=table_elements,
        )

        return PaperMetadata(
            paper_id=str(uuid4()),
            source="upload",
            source_id="",
            title=self.extract_title(full_text),
            authors=self.extract_authors(full_text),
            abstract=self.extract_abstract(full_text),
            year=self.extract_year(full_text, document_metadata=doc.metadata),
            sections=sections,
            figures=figures,
            tables=tables,
            layout_elements=layout_elements,
            references=self.extract_references(full_text),
            full_text=full_text,
            parse_status="done",
        )

    @staticmethod
    def extract_year(
        text: str,
        *,
        document_metadata: dict[str, Any] | None = None,
        source_hint: str = "",
    ) -> int | None:
        """Best-effort publication year from arXiv IDs, front matter, or PDF metadata.

        A random four-digit number in the paper body is deliberately not accepted:
        model names, dataset versions, and reference years are too easy to mistake
        for the publication year.
        """
        current_limit = 2100
        combined_hint = f"{source_hint}\n{text[:8000]}"

        arxiv_match = re.search(r"(?:arxiv[:/]|/pdf/)(\d{2})(?:\d{2})?\.\d{4,5}", combined_hint, re.IGNORECASE)
        if arxiv_match:
            year = 2000 + int(arxiv_match.group(1))
            if 2007 <= year <= current_limit:
                return year

        labeled_patterns = (
            r"(?:published|accepted|received|copyright|proceedings|conference|journal|volume|©|\(c\))[^\n]{0,100}?\b((?:19|20)\d{2})\b",
            r"\b((?:19|20)\d{2})\b[^\n]{0,80}?(?:IEEE|ACM|Springer|Elsevier|USENIX|NeurIPS|ICML|ICLR|AAAI|IJCAI|CVPR|ACL|EMNLP)",
        )
        for pattern in labeled_patterns:
            match = re.search(pattern, text[:8000], re.IGNORECASE)
            if match:
                year = int(match.group(1))
                if 1900 <= year <= current_limit:
                    return year

        metadata = document_metadata or {}
        for key in ("creationDate", "modDate", "CreationDate", "ModDate"):
            value = str(metadata.get(key) or "")
            match = re.search(r"(?:D:)?((?:19|20)\d{2})", value)
            if match:
                year = int(match.group(1))
                if 1900 <= year <= current_limit:
                    return year

        explicit_hint = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", source_hint)
        if explicit_hint:
            year = int(explicit_hint.group(1))
            if 1900 <= year <= current_limit:
                return year
        return None

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
        for block in PDFParser._sort_blocks_for_reading(page, PDFParser._page_text_blocks(page)):
            block_rect = fitz.Rect(block.get("bbox", (0, 0, 0, 0)))
            if excluded_regions and PDFParser._inside_any(block_rect, excluded_regions):
                continue
            block_text = PDFParser._clean_text_block_for_sections(str(block.get("text", "")))
            if caption_pattern.match(re.sub(r"\s+", " ", block_text).strip()):
                continue
            if block_text:
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
            figure_number = figure.figure_id.partition(":")[2]
            reference_pattern = re.compile(
                rf"(?:Figure|Fig(?:ure)?\.?)\s*{re.escape(figure_number)}\b(?!\s*:)",
                re.IGNORECASE,
            )
            referenced_by = [
                section
                for section in sections
                if figure_number and reference_pattern.search(section.content)
            ]
            if referenced_by:
                figure.section_id = referenced_by[0].section_id
                continue

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

            matching = [
                section for section in sections
                if section.start_page is not None
                and figure.page is not None
                and section.start_page <= figure.page
            ]
            if matching:
                figure.section_id = matching[-1].section_id

    def _extract_tables(
        self,
        doc: fitz.Document,
        sections: list[PaperSection],
    ) -> tuple[list[PaperTable], list[PaperLayoutElement]]:
        """Extract structured tables when PyMuPDF can detect them.

        Structured table recovery is version-dependent in PyMuPDF. When available,
        ``find_tables`` supplies a bbox and extracted cell grid. Non-figure screenshots
        are intentionally forbidden, so tables are returned only as structured cells.
        """
        tables: list[PaperTable] = []
        elements: list[PaperLayoutElement] = []
        for page_index, page in enumerate(doc):
            finder = getattr(page, "find_tables", None)
            if finder is None:
                continue
            try:
                found = finder()
                raw_tables = list(getattr(found, "tables", []) or [])
            except Exception as exc:
                logger.debug("PyMuPDF table detection failed on page %d: %s", page_index + 1, exc)
                continue
            for table_index, table in enumerate(raw_tables, start=1):
                bbox = getattr(table, "bbox", None)
                if not bbox:
                    continue
                rect = self._expand_rect(fitz.Rect(bbox), page.rect, margin=4)
                if rect.is_empty or rect.width < 30 or rect.height < 20:
                    continue
                data: list[list[str]] = []
                try:
                    data = [
                        [str(cell or "").strip() for cell in row]
                        for row in (table.extract() or [])
                    ]
                except Exception:
                    data = []
                section_id = self._section_for_page(sections, page_index + 1)
                table_id = f"table:{len(tables) + 1}"
                tables.append(PaperTable(
                    table_id=table_id,
                    caption=f"Table {len(tables) + 1}",
                    page=page_index + 1,
                    section_id=section_id,
                    bbox=self._rect_values(rect),
                    headers=data[0] if data else [],
                    data=data[1:] if len(data) > 1 else data,
                ))
                elements.append(PaperLayoutElement(
                    element_id=table_id,
                    element_type="table",
                    page=page_index + 1,
                    bbox=self._rect_values(rect),
                    section_id=section_id,
                    text=f"Table {len(tables)}",
                    caption=f"Table {len(tables)}",
                    raw_text=self._table_to_markdown(data),
                    table_data=data,
                    confidence=0.86 if data else 0.68,
                ))
        return tables, elements

    def _extract_layout_elements(
        self,
        *,
        doc: fitz.Document,
        sections: list[PaperSection],
        figures: list[PaperFigure],
        table_elements: list[PaperLayoutElement],
    ) -> list[PaperLayoutElement]:
        """Build page/bbox ordered elements for the reflow reader."""
        elements: list[PaperLayoutElement] = []
        table_regions = {
            (element.page, tuple(round(value, 2) for value in element.bbox))
            for element in table_elements
            if element.page and len(element.bbox) == 4
        }
        figure_regions_by_page: dict[int, list[fitz.Rect]] = {}
        for figure in figures:
            if figure.page and len(figure.bbox) == 4:
                figure_regions_by_page.setdefault(figure.page, []).append(fitz.Rect(figure.bbox))

        current_section = sections[0].section_id if sections else ""
        for page_index, page in enumerate(doc):
            page_number = page_index + 1
            text_blocks = self._page_text_blocks(page)
            formula_buffer: list[dict[str, Any]] = []

            def flush_formula_buffer() -> None:
                nonlocal formula_buffer
                if formula_buffer:
                    self._append_formula_element(elements, page, page_number, formula_buffer)
                    formula_buffer = []

            for block in self._sort_blocks_for_reading(page, text_blocks):
                rect = fitz.Rect(block["bbox"])
                text = str(block["text"]).strip()
                if not text:
                    continue
                if self._inside_any(rect, figure_regions_by_page.get(page_number, [])):
                    continue
                if self._inside_table_region(rect, table_regions, page_number):
                    continue
                section = self._match_section_heading(text, sections)
                if section is not None:
                    flush_formula_buffer()
                    current_section = section.section_id
                    elements.append(PaperLayoutElement(
                        element_id=f"layout:{len(elements) + 1}",
                        element_type="heading",
                        page=page_number,
                        bbox=self._rect_values(rect),
                        section_id=section.section_id,
                        level=section.level,
                        text=section.title,
                        raw_text=text,
                        confidence=0.9,
                    ))
                    continue
                if self._looks_like_noise_line(text, page.rect, rect):
                    continue
                current_section = self._advance_section_after_boundary(
                    sections,
                    current_section,
                    page_number,
                )
                if self._looks_like_formula_block(text, rect, page.rect):
                    if formula_buffer and not self._can_merge_formula_block(formula_buffer[-1]["rect"], rect):
                        flush_formula_buffer()
                    formula_section = self._section_for_text_block(text, sections) or current_section
                    formula_buffer.append({
                        "rect": rect,
                        "text": text,
                        "section_id": formula_section,
                    })
                    continue
                flush_formula_buffer()
                paragraph_text = self._clean_layout_text(text)
                paragraph_section = self._section_for_text_block(paragraph_text, sections) or current_section
                current_section = paragraph_section
                elements.append(PaperLayoutElement(
                    element_id=f"layout:{len(elements) + 1}",
                    element_type="paragraph",
                    page=page_number,
                    bbox=self._rect_values(rect),
                    section_id=paragraph_section,
                    text=paragraph_text,
                    raw_text=text,
                    confidence=0.82,
                ))
            flush_formula_buffer()

        for figure in figures:
            if not figure.page or len(figure.bbox) != 4:
                continue
            elements.append(PaperLayoutElement(
                element_id=f"layout:figure:{figure.figure_id}",
                element_type="figure",
                page=figure.page,
                bbox=figure.bbox,
                section_id=figure.section_id or self._section_for_page(sections, figure.page),
                text=figure.caption,
                caption=figure.caption,
                asset_name=figure.asset_name,
                width=figure.width,
                height=figure.height,
                confidence=0.84,
            ))

        elements.extend(table_elements)
        elements.sort(key=lambda item: (
            item.page or 0,
            item.bbox[1] if len(item.bbox) == 4 else 0,
            item.bbox[0] if len(item.bbox) == 4 else 0,
            item.element_type,
        ))
        for index, element in enumerate(elements, start=1):
            element.reading_order = index
            if not element.element_id or element.element_id.startswith("layout:"):
                element.element_id = f"layout:{index}"
        return elements

    def _append_formula_element(
        self,
        elements: list[PaperLayoutElement],
        page: fitz.Page,
        page_number: int,
        formula_blocks: list[dict[str, Any]],
    ) -> None:
        """Merge adjacent formula fragments into one text formula element."""
        if not formula_blocks:
            return
        union = fitz.Rect(formula_blocks[0]["rect"])
        for block in formula_blocks[1:]:
            union |= fitz.Rect(block["rect"])
        if union.is_empty or union.width < 10 or union.height < 4:
            return
        text = " ".join(
            re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
            for block in formula_blocks
            if str(block.get("text", "")).strip()
        )
        raw_text = "\n".join(str(block.get("text", "")) for block in formula_blocks)
        formula_text = self._normalize_formula_text(text or raw_text)
        elements.append(PaperLayoutElement(
            element_id=f"layout:{len(elements) + 1}",
            element_type="formula",
            page=page_number,
            bbox=self._rect_values(union),
            section_id=str(formula_blocks[0].get("section_id", "")),
            text=formula_text,
            raw_text=raw_text,
            latex=formula_text,
            confidence=0.78 if len(formula_blocks) > 1 else 0.68,
        ))

    @staticmethod
    def _page_text_blocks(page: fitz.Page) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0 or not block.get("bbox"):
                continue
            lines: list[str] = []
            max_size = 0.0
            bold = False
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span.get("text", "") for span in spans).strip()
                if text and not PDFParser._looks_like_running_header(text):
                    lines.append(text)
                for span in spans:
                    max_size = max(max_size, float(span.get("size", 0) or 0))
                    bold = bold or "bold" in str(span.get("font", "")).lower()
            text = "\n".join(lines).strip()
            if text:
                blocks.append({
                    "bbox": block["bbox"],
                    "text": text,
                    "font_size": max_size,
                    "bold": bold,
                })
        return blocks

    @staticmethod
    def _sort_blocks_for_reading(page: fitz.Page, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(blocks) < 4:
            return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        page_width = page.rect.width
        mid = page.rect.x0 + page_width / 2
        left = [item for item in blocks if (item["bbox"][0] + item["bbox"][2]) / 2 < mid]
        right = [item for item in blocks if (item["bbox"][0] + item["bbox"][2]) / 2 >= mid]
        has_columns = len(left) >= 3 and len(right) >= 3
        if not has_columns:
            return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0]))

        def key(item: dict[str, Any]) -> tuple[int, float, float]:
            rect = fitz.Rect(item["bbox"])
            if rect.width >= page_width * 0.62:
                return (0, rect.y0, rect.x0)
            column = 0 if (rect.x0 + rect.x1) / 2 < mid else 1
            return (1 + column, rect.y0, rect.x0)

        return sorted(blocks, key=key)

    @staticmethod
    def _inside_any(rect: fitz.Rect, regions: list[fitz.Rect]) -> bool:
        center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        return any(region.contains(center) or region.intersects(rect) and (region & rect).get_area() > rect.get_area() * 0.6 for region in regions)

    @staticmethod
    def _inside_table_region(
        rect: fitz.Rect,
        table_regions: set[tuple[int | None, tuple[float, ...]]],
        page_number: int,
    ) -> bool:
        for page, bbox in table_regions:
            if page != page_number or len(bbox) != 4:
                continue
            region = fitz.Rect(bbox)
            if region.intersects(rect) and (region & rect).get_area() > rect.get_area() * 0.45:
                return True
        return False

    @classmethod
    def _match_section_heading(
        cls,
        text: str,
        sections: list[PaperSection],
    ) -> PaperSection | None:
        normalized = cls._normalize_heading_text(text)
        if not normalized or cls._looks_like_running_header(text):
            return None
        for section in sections:
            title = cls._normalize_heading_text(section.title)
            bare_title = cls._normalize_heading_text(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", section.title))
            if normalized in {title, bare_title}:
                return section
            if title and (normalized.startswith(title) or title.startswith(normalized)):
                return section
            if bare_title and (normalized.startswith(bare_title) or bare_title.startswith(normalized)):
                return section
            title_tokens = [token for token in bare_title.split() if len(token) > 1]
            if title_tokens and len(title_tokens) <= 8 and all(token in normalized for token in title_tokens):
                return section
        return None

    @staticmethod
    def _normalize_heading_text(text: str) -> str:
        value = re.sub(r"\s+", " ", text).strip().lower()
        value = re.sub(r"^\s*(\d+(?:\.\d+)*)\.?\s+", lambda m: m.group(1).replace(".", " ") + " ", value)
        value = re.sub(r"[^a-z0-9一-鿿 ]", "", value)
        return value.strip()

    @staticmethod
    def _looks_like_running_header(text: str) -> bool:
        value = re.sub(r"\s+", " ", text).strip().lower()
        if not value:
            return False
        header_phrases = (
            "published as a conference paper",
            "conference paper at iclr",
            "international conference on learning representations",
            "proceedings of",
            "workshop on",
            "preprint",
            "under review",
            "submitted to",
            "accepted at",
            "camera ready",
            "anonymous authors",
        )
        if any(phrase in value for phrase in header_phrases):
            return True
        if re.fullmatch(r"(?:iclr|neurips|nips|icml|acl|emnlp|cvpr|iccv|eccv|aaai|ijcai)\s+\d{4}", value):
            return True
        return False

    @staticmethod
    def _section_for_page(sections: list[PaperSection], page_number: int) -> str:
        candidates = [
            section for section in sections
            if section.start_page is not None
            and section.end_page is not None
            and section.start_page <= page_number <= section.end_page
        ]
        if candidates:
            return candidates[-1].section_id
        before = [
            section for section in sections
            if section.start_page is not None and section.start_page <= page_number
        ]
        return before[-1].section_id if before else (sections[0].section_id if sections else "")

    @staticmethod
    def _advance_section_after_boundary(
        sections: list[PaperSection],
        current_section: str,
        page_number: int,
    ) -> str:
        if not sections:
            return current_section
        current_index = next(
            (index for index, section in enumerate(sections) if section.section_id == current_section),
            0,
        )
        current = sections[current_index]
        if current.end_page is None or page_number <= current.end_page:
            return current_section
        candidates = [
            (index, section)
            for index, section in enumerate(sections)
            if index > current_index
            and section.start_page is not None
            and section.start_page <= page_number
        ]
        return candidates[-1][1].section_id if candidates else current_section

    @classmethod
    def _section_for_text_block(
        cls,
        text: str,
        sections: list[PaperSection],
    ) -> str:
        probe = cls._normalize_content_probe(text)
        if len(probe) < 32:
            return ""
        best: tuple[int, str] = (0, "")
        for section in sections:
            content = cls._normalize_content_probe(section.content)
            if not content:
                continue
            if probe in content:
                return section.section_id
            words = [word for word in probe.split() if len(word) > 3][:18]
            if not words:
                continue
            score = sum(1 for word in words if word in content)
            if score > best[0]:
                best = (score, section.section_id)
        return best[1] if best[0] >= 5 else ""

    @staticmethod
    def _normalize_content_probe(text: str) -> str:
        value = re.sub(r"\s+", " ", text).strip().lower()
        value = re.sub(r"[^a-z0-9一-鿿 ]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _looks_like_noise_line(text: str, page_rect: fitz.Rect, rect: fitz.Rect) -> bool:
        value = re.sub(r"\s+", " ", text).strip()
        if re.fullmatch(r"\d{1,3}", value):
            return True
        if PDFParser._looks_like_running_header(value):
            return True
        if value == "Preprint.":
            return True
        if re.match(r"^arXiv:\S+\s+\[", value):
            return True
        if rect.y0 < page_rect.y0 + 18 and len(value) < 80:
            return True
        if rect.y1 > page_rect.y1 - 18 and len(value) < 80:
            return True
        return False

    @classmethod
    def _clean_text_block_for_sections(cls, text: str) -> str:
        lines: list[str] = []
        for raw_line in text.replace("\r", "\n").split("\n"):
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            if cls._looks_like_running_header(line):
                continue
            if re.fullmatch(r"\d+(?:\.\d+)*\.?", line):
                lines.append(line)
                continue
            if cls._looks_like_section_noise_line(line):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @classmethod
    def _looks_like_section_noise_line(cls, text: str) -> bool:
        """Drop PDF math glyph leftovers while keeping normal prose."""
        value = re.sub(r"\s+", " ", text).strip()
        if not value:
            return True
        if cls._looks_like_running_header(value):
            return True
        if re.fullmatch(r"\d{1,3}", value):
            return True
        if value == "Preprint." or re.match(r"^arXiv:\S+\s+\[", value):
            return True
        if any(character in value for character in "□�"):
            return True
        if re.fullmatch(r"[!\"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~−]+", value):
            return True

        normalized = value.strip("`'\".,;:()[]{} ")
        lower = normalized.lower()
        if lower in {"min", "max", "argmax", "argmin", "clip"}:
            return True

        math_symbols = set("=∑∏∫√≤≥≈≠⊆⊂⊇∈∀∃±×÷·→←↔∞αβγδΔλμθσΣΠΩ−{}_^<>|/~")
        symbol_count = sum(1 for character in value if character in math_symbols)
        alnum_count = sum(1 for character in value if character.isalnum())
        if len(value) <= 14 and symbol_count:
            return True
        if (
            len(value) <= 4
            and alnum_count <= 2
            and not re.search(r"[A-Za-z一-鿿]{2,}", value)
        ):
            return True
        if re.fullmatch(r"[A-Z]\s+[A-Z]", value):
            return True
        if re.fullmatch(r"\d+\s*[A-Za-z]{1,3}", value):
            return True
        return False

    @staticmethod
    def _looks_like_formula_block(text: str, rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
        value = re.sub(r"\s+", " ", text).strip()
        if not 2 <= len(value) <= 420:
            return False
        math_symbols = set("=∑∏∫√≤≥≈≠⊆⊂⊇∈∀∃±×÷·→←↔∞αβγδΔλμθσΣΠΩ−{}_^<>|/")
        symbol_count = sum(1 for character in value if character in math_symbols)
        if symbol_count == 0:
            return False
        if re.match(r"^(?:Figure|Fig|Table|Tab)\b", value, re.IGNORECASE):
            return False
        centered = abs(((rect.x0 + rect.x1) / 2) - ((page_rect.x0 + page_rect.x1) / 2)) < page_rect.width * 0.22
        compact_lines = text.count("\n") <= 5
        alpha_ratio = sum(character.isalpha() for character in value) / max(len(value), 1)
        return compact_lines and (centered or symbol_count >= 2) and alpha_ratio < 0.82

    @staticmethod
    def _can_merge_formula_block(previous: fitz.Rect, current: fitz.Rect) -> bool:
        vertical_gap = current.y0 - previous.y1
        if vertical_gap < -8:
            return True
        if vertical_gap > 58:
            return False
        overlap = min(previous.x1, current.x1) - max(previous.x0, current.x0)
        min_width = max(1.0, min(previous.width, current.width))
        centers_close = abs(((previous.x0 + previous.x1) / 2) - ((current.x0 + current.x1) / 2)) < 180
        return overlap / min_width > 0.12 or centers_close

    @staticmethod
    def _normalize_formula_text(text: str) -> str:
        value = re.sub(r"\s+", " ", text).strip()
        replacements = {
            "≤": r"\le",
            "≥": r"\ge",
            "≠": r"\ne",
            "≈": r"\approx",
            "∑": r"\sum",
            "∏": r"\prod",
            "∫": r"\int",
            "√": r"\sqrt",
            "∞": r"\infty",
            "→": r"\to",
            "←": r"\leftarrow",
            "×": r"\times",
            "÷": r"\div",
            "α": r"\alpha",
            "β": r"\beta",
            "γ": r"\gamma",
            "δ": r"\delta",
            "Δ": r"\Delta",
            "λ": r"\lambda",
            "μ": r"\mu",
            "θ": r"\theta",
            "σ": r"\sigma",
            "Σ": r"\Sigma",
            "Π": r"\Pi",
            "Ω": r"\Omega",
        }
        for source, target in replacements.items():
            value = value.replace(source, target)
        return value

    @staticmethod
    def _table_to_markdown(data: list[list[str]]) -> str:
        if not data:
            return ""
        width = max((len(row) for row in data), default=0)
        rows = [
            [str(cell or "").replace("|", r"\|").strip() for cell in row] + [""] * (width - len(row))
            for row in data
        ]
        if not rows:
            return ""
        header = rows[0]
        separator = ["---"] * width
        body = rows[1:]

        def line(cells: list[str]) -> str:
            return "| " + " | ".join(cells) + " |"

        return "\n".join([line(header), line(separator), *(line(row) for row in body)])

    @staticmethod
    def _clean_layout_text(text: str) -> str:
        lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
        if not lines:
            return ""
        value = " ".join(lines)
        value = re.sub(r"-\s+([a-z])", r"\1", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _expand_rect(rect: fitz.Rect, page_rect: fitz.Rect, margin: float = 5) -> fitz.Rect:
        return fitz.Rect(
            max(page_rect.x0, rect.x0 - margin),
            max(page_rect.y0, rect.y0 - margin),
            min(page_rect.x1, rect.x1 + margin),
            min(page_rect.y1, rect.y1 + margin),
        )

    @staticmethod
    def _render_clip(page: fitz.Page, rect: fitz.Rect) -> tuple[bytes, int, int]:
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(2, 2),
            colorspace=fitz.csRGB,
            alpha=False,
            clip=rect,
        )
        return pixmap.tobytes("png"), pixmap.width, pixmap.height

    @staticmethod
    def _rect_values(rect: fitz.Rect) -> list[float]:
        return [round(value, 2) for value in (rect.x0, rect.y0, rect.x1, rect.y1)]

    # ── 章节提取 ──

    def extract_sections(self, text: str) -> list[PaperSection]:
        """识别章节结构，同时排除页码、公式编号、年份和图表坐标。

        PDF 文本经常把章节编号与标题拆成两行，例如 ``4.1`` 下一行才是
        ``Coarse-grained Memory Retrieval``。旧实现会把任何数字开头的行都
        当作标题，导致公式和参考文献被误切成章节。这里同时校验标题形态与
        顶层章节的连续性。
        """
        lines = self._normalize_section_lines(text)
        headings: list[tuple[int, int, str, str, int]] = []
        seen_ids: set[str] = set()
        current_top = 0
        references_seen = False
        next_appendix_letter = "A"

        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue
            if self._looks_like_running_header(line):
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
    def _normalize_section_lines(text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            abstract = re.match(r"^(Abstract|摘要)\s*[:：.\-]?\s+(.{40,})$", line, re.IGNORECASE)
            if abstract:
                lines.append(abstract.group(1))
                lines.append(abstract.group(2).strip())
                continue
            references = re.match(r"^(References|Bibliography|参考文献)\s+(.{40,})$", line, re.IGNORECASE)
            if references:
                lines.append(references.group(1))
                lines.append(references.group(2).strip())
                continue
            lines.append(raw_line)
        return lines

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
        if PDFParser._looks_like_running_header(value):
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
            if cleaned and not PDFParser._looks_like_section_noise_line(cleaned):
                paragraphs.append(cleaned)
            buffer = ""

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                flush()
                continue
            if PDFParser._looks_like_running_header(line) or PDFParser._looks_like_section_noise_line(line):
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
            if self._looks_like_running_header(line):
                continue
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
        lines = self._normalize_section_lines(text)
        for index, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not re.fullmatch(r"(Abstract|摘要)", line, re.IGNORECASE):
                continue
            body: list[str] = []
            for candidate_index, candidate in enumerate(lines[index + 1:], start=index + 1):
                value = candidate.strip()
                if not value:
                    if body:
                        body.append("")
                    continue
                if self._looks_like_abstract_boundary(value, lines, candidate_index):
                    break
                if self._looks_like_running_header(value) or self._looks_like_section_noise_line(value):
                    continue
                body.append(value)
            abstract = " ".join(
                paragraph
                for paragraph in self._lines_to_paragraphs(body)
                if paragraph
            ).strip()
            if len(abstract) > 50:
                return abstract[:3000]

        inline = re.search(
            r"\b(?:Abstract|摘要)\b\s*[:：.\-]?\s+(.{80,}?)(?=\n\s*(?:1(?:\.|\s)|Introduction\b|引言\b|References\b|Bibliography\b))",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if inline:
            abstract = re.sub(r"\s+", " ", inline.group(1)).strip()
            if len(abstract) > 50:
                return abstract[:3000]

        # 回退：取前 2000 字符中较长的段落
        first_chunk = text[:2000]
        paragraphs = [p.strip() for p in first_chunk.split("\n\n") if len(p.strip()) > 100]
        return paragraphs[0][:2000] if paragraphs else ""

    def _looks_like_abstract_boundary(
        self,
        line: str,
        lines: list[str],
        current_index: int,
    ) -> bool:
        if re.fullmatch(r"(References|Bibliography|参考文献)", line, re.IGNORECASE):
            return True
        if re.fullmatch(r"(Introduction|引言)", line, re.IGNORECASE):
            return True
        inline = re.fullmatch(r"(\d+(?:\.\d+)*)\.?\s+(.+)", line)
        if inline and self._looks_like_heading_title(inline.group(2).strip()):
            return True
        number_only = re.fullmatch(r"(\d+(?:\.\d+)*)\.?", line)
        if number_only:
            next_index = self._next_nonempty_line(lines, current_index + 1)
            if next_index is not None and self._looks_like_heading_title(lines[next_index].strip()):
                return True
        return False

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
