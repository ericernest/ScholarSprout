"""知识图谱构建触发工具 — 允许 Agent 为论文章节构建 KG 节点和边。"""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool, ToolSpec


class KGBuildTool(BaseTool):
    """Agent 可调用的 KG 构建工具。"""

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="kg_build",
            description=(
                "为论文指定章节构建知识图谱节点和边。"
                "通常在阅读完一个章节后调用，将章节内容转化为结构化的 KG 元素。"
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
                        "description": "章节 ID（如 sec:3.2）",
                    },
                    "section_content": {
                        "type": "string",
                        "description": "章节原文内容",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["full_paper", "revealed_section"],
                        "description": "构建整篇论文 KG 或返回当前阶段已展开子图",
                        "default": "full_paper",
                    },
                },
                "required": ["paper_id"],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """触发渐进式 KG 构建。"""
        engine = _get_kg_engine()
        builder = _get_kg_builder()

        if engine is None:
            return {"error": "KG 引擎未初始化"}
        if builder is None:
            return {"error": "KG 构建器未初始化"}

        try:
            paper_id = str(arguments.get("paper_id", ""))
            section_id = str(arguments.get("section_id", ""))
            paper_data = _load_paper_data(paper_id) or {
                "paper_id": paper_id,
                "title": arguments.get("title", ""),
                "abstract": "",
                "sections": [
                    {
                        "section_id": section_id or "sec:unknown",
                        "title": section_id or "Provided Section",
                        "level": 1,
                        "content": str(arguments.get("section_content", "")),
                    }
                ],
            }
            result = builder.ensure_full_paper_kg(
                paper_id=paper_id,
                paper_data=paper_data,
            )
            revealed = builder.get_revealed_subgraph(paper_id, "general")

            return {
                "new_nodes": len(result.new_nodes),
                "new_edges": len(result.new_edges),
                "node_summary": [
                    {
                        "node_type": n.node_type,
                        "label": n.label,
                        "node_id": n.node_id,
                    }
                    for n in result.new_nodes
                ],
                "section_type": result.section_type,
                "mode": "full_paper_once_full_display",
                "revealed_kg": revealed,
            }
        except Exception as e:
            return {"error": f"KG 构建失败: {e}"}


# ── 依赖注入 ──

_kg_builder_instance = None


def set_kg_builder(builder) -> None:
    """设置全局 KG 构建器实例。"""
    global _kg_builder_instance
    _kg_builder_instance = builder


def _get_kg_builder():
    return _kg_builder_instance


def _load_paper_data(paper_id: str) -> dict[str, Any] | None:
    if not paper_id:
        return None
    try:
        from handlers.paper_reading.harness.storage import PaperReadingStorage

        return PaperReadingStorage().load_paper(paper_id)
    except Exception:
        return None
