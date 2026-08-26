"""PDF 解析工具 — 允许 Agent 解析已上传的 PDF 论文。"""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool, ToolSpec


class PDFParseTool(BaseTool):
    """Agent 可调用的 PDF 解析工具。"""

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="pdf_parse",
            description=(
                "解析已上传的 PDF 论文，提取指定章节或全文内容。"
                "当需要获取论文的具体章节文本进行分析时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": "论文内部 ID",
                    },
                    "section_id": {
                        "type": "string",
                        "description": "要提取的章节 ID，不指定则返回章节索引列表",
                    },
                },
                "required": ["paper_id"],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 PDF 章节提取。"""
        from handlers.paper_reading.harness.storage import PaperReadingStorage

        storage = PaperReadingStorage()
        paper_id = str(arguments.get("paper_id", "")).strip()
        if not paper_id:
            return {"error": "paper_id 不能为空"}

        paper = storage.load_paper(paper_id)
        if paper is None:
            return {"error": f"论文未找到: {paper_id}"}

        section_id = arguments.get("section_id", "")
        if section_id:
            sections = paper.get("sections", [])
            for s in sections:
                if s.get("section_id") == section_id:
                    return {
                        "section_id": s["section_id"],
                        "title": s.get("title", ""),
                        "content": s.get("content", "")[:10000],
                        "content_length": len(s.get("content", "")),
                        "paragraphs_count": len(s.get("paragraphs", [])),
                    }
            return {"error": f"章节未找到: {section_id}"}

        # 返回章节索引
        return {
            "sections": [
                {
                    "section_id": s.get("section_id", ""),
                    "title": s.get("title", ""),
                    "level": s.get("level", 1),
                    "content_length": len(s.get("content", "")),
                }
                for s in paper.get("sections", [])
            ],
            "title": paper.get("title", ""),
            "abstract": paper.get("abstract", "")[:500],
        }
