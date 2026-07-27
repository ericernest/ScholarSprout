"""论文元数据标准化 Pydantic 模型。

统一多源（arXiv、Semantic Scholar、DBLP、OpenAlex、用户上传）论文元数据格式。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class Author(BaseModel):
    """论文作者。"""

    name: str = ""
    affiliation: str | None = None
    email: str | None = None


class PaperSection(BaseModel):
    """论文章节（PDF 解析后的结构化表示）。"""

    section_id: str = Field(
        default="",
        description="章节唯一标识，如 'sec:3.2'",
    )
    title: str = Field(
        default="",
        description="章节标题，如 '3.2 Attention Mechanism'",
    )
    level: int = Field(
        default=1,
        ge=1,
        le=6,
        description="标题层级（1-6）",
    )
    content: str = Field(
        default="",
        description="章节原始文本内容",
    )
    paragraphs: list[str] = Field(
        default_factory=list,
        description="按段落分割后的文本列表",
    )
    start_page: int | None = Field(default=None, ge=1)
    end_page: int | None = Field(default=None, ge=1)


class PaperFigure(BaseModel):
    """论文中的图表。"""

    figure_id: str = ""
    caption: str = ""
    figure_type: Literal["architecture", "chart", "table_image", "other"] = "other"
    page: int | None = Field(default=None, ge=1)


class PaperTable(BaseModel):
    """论文中的表格（结构化数据）。"""

    table_id: str = ""
    caption: str = ""
    page: int | None = Field(default=None, ge=1)
    data: list[list[str]] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)


class PaperReference(BaseModel):
    """论文引用。"""

    ref_id: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    arxiv_id: str = ""
    doi: str = ""


class PaperMetadata(BaseModel):
    """标准化论文元数据。

    统一 arXiv、Semantic Scholar、DBLP、OpenAlex 等数据源的差异字段，
    为上层 KG 引擎和 Agent 提供一致的论文数据结构。
    """

    paper_id: str = Field(
        default="",
        description="内部唯一 ID（UUID v4）",
    )
    source: Literal["arxiv", "semantic_scholar", "dblp", "openalex", "upload"] = Field(
        default="upload",
        description="论文来源",
    )
    source_id: str = Field(
        default="",
        description="来源系统的原始 ID（如 arxiv ID）",
    )
    title: str = ""
    authors: list[Author] = Field(default_factory=list)
    abstract: str = ""
    published_date: date | None = None
    updated_date: date | None = None
    year: int | None = Field(
        default=None,
        description="发表年份（四位数整数）",
    )
    categories: list[str] = Field(
        default_factory=list,
        description="学科分类标签",
    )
    keywords: list[str] = Field(default_factory=list)
    doi: str = ""
    url: str = ""
    pdf_url: str = ""
    citation_count: int | None = None
    venue: str = Field(default="", description="发表会议/期刊名称")

    # ── PDF 解析后填充的结构化内容 ──
    sections: list[PaperSection] = Field(default_factory=list)
    figures: list[PaperFigure] = Field(default_factory=list)
    tables: list[PaperTable] = Field(default_factory=list)
    references: list[PaperReference] = Field(default_factory=list)
    full_text: str = Field(default="", description="解析后的全文文本")

    # ── 内部标记 ──
    parse_status: Literal["pending", "parsing", "done", "failed"] = "pending"
    stored_at: str = Field(default="", description="存入存储的时间戳")

    def to_summary(self) -> dict:
        """返回不含全文内容的摘要字典。"""
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": [a.name for a in self.authors],
            "year": self.year,
            "venue": self.venue,
            "source": self.source,
            "url": self.url,
            "sections_count": len(self.sections),
            "abstract": self.abstract[:500],
        }

    def get_section_by_id(self, section_id: str) -> PaperSection | None:
        """按 section_id 查找章节。"""
        for section in self.sections:
            if section.section_id == section_id:
                return section
        return None

    def get_section_by_title(self, keyword: str) -> list[PaperSection]:
        """按标题关键词模糊查找章节。"""
        kw = keyword.lower()
        return [s for s in self.sections if kw in s.title.lower()]
