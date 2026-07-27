"""论文检索工具 — 允许 Agent 在对话中搜索论文。"""

from __future__ import annotations

import asyncio
from typing import Any

from tools.base import BaseTool, ToolSpec


class PaperSearchTool(BaseTool):
    """Agent 可调用的论文搜索工具。

    支持的来源: arXiv, Semantic Scholar
    """

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="paper_search",
            description=(
                "搜索学术论文。支持 arXiv 和 Semantic Scholar 数据源。"
                "OpenAlex 负责快速解析 arXiv ID，并在两个主源失败时自动降级。"
                "返回论文标题、作者、年份、摘要和链接。"
                "当用户需要查找特定主题的论文时使用此工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 'attention mechanism for NLP'",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["arxiv", "semantic_scholar", "all"],
                        "description": "搜索来源",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回数量（默认 5）",
                    },
                },
                "required": ["query"],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行论文搜索。"""
        from handlers.paper_reading.pipeline.sources import PaperPipeline

        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": "搜索关键词不能为空", "papers": []}

        source = arguments.get("source", "all")
        max_results = min(int(arguments.get("max_results", 5)), 20)

        pipeline = PaperPipeline()
        try:
            results = asyncio.run(
                pipeline.search(
                    query=query,
                    sources=None if source == "all" else [source],
                    max_results=max_results,
                )
            )
        except Exception as e:
            return {"error": f"搜索失败: {e}", "papers": []}

        return {
            "papers": [
                {
                    "paper_id": p.paper_id,
                    "title": p.title,
                    "authors": [a.name for a in p.authors],
                    "year": p.year,
                    "abstract": (
                        p.abstract[:300] + "..."
                        if len(p.abstract) > 300
                        else p.abstract
                    ),
                    "source": p.source,
                    "url": p.url,
                    "pdf_url": p.pdf_url,
                }
                for p in results
            ],
            "count": len(results),
        }
