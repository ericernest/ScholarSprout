"""知识图谱查询工具 — 允许 Agent 查询论文知识图谱。"""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool, ToolSpec


class KGQueryTool(BaseTool):
    """Agent 可调用的知识图谱查询工具。"""

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="kg_query",
            description=(
                "查询论文知识图谱，获取节点关系、推理路径、实验证据等结构化信息。"
                "当 Agent 需要回答关于论文内部结构关系的问题时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["path", "neighbors", "search"],
                        "description": "查询类型: path(两实体关系路径), neighbors(节点邻域), search(按关键词搜索)",
                    },
                    "source_label": {
                        "type": "string",
                        "description": "源实体标签（path 查询）",
                    },
                    "target_label": {
                        "type": "string",
                        "description": "目标实体标签（path 查询）",
                    },
                    "node_id": {
                        "type": "string",
                        "description": "节点 ID（neighbors 查询）",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词（search 查询）",
                    },
                    "question": {
                        "type": "string",
                        "description": "自然语言问题，用于 KG 驱动问答",
                    },
                    "paper_id": {
                        "type": "string",
                        "description": "限定论文 ID",
                    },
                },
                "required": ["query_type"],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 KG 查询。"""
        # 依赖注入: 通过全局上下文获取 engine 实例
        engine = _get_kg_engine()
        if engine is None:
            return {"error": "KG 引擎未初始化"}

        query_type = arguments.get("query_type", "search")

        try:
            if query_type == "path":
                paths = engine.query_path(
                    str(arguments.get("source_label", "")),
                    str(arguments.get("target_label", "")),
                )
                return {"paths": paths, "count": len(paths)}
            elif query_type == "neighbors":
                neighbors = engine.get_neighbors(
                    str(arguments.get("node_id", "")),
                    depth=arguments.get("depth", 1),
                )
                return {"neighbors": neighbors}
            elif query_type == "search":
                from paper_reading.kg.query import KGQueryEngine

                question = str(arguments.get("question") or arguments.get("keyword", ""))
                answer = KGQueryEngine(engine).answer(
                    question=question,
                    paper_id=str(arguments.get("paper_id", "")),
                    query_type="search",
                )
                answer["nodes"] = answer.get("matched_nodes", [])
                answer["count"] = len(answer["nodes"])
                return answer
            else:
                return {"error": f"不支持的查询类型: {query_type}"}
        except Exception as e:
            return {"error": f"KG 查询失败: {e}"}


# ── 依赖注入 ──

_kg_engine_instance = None


def set_kg_engine(engine) -> None:
    """设置全局 KG 引擎实例（在 gateway 初始化后调用）。"""
    global _kg_engine_instance
    _kg_engine_instance = engine


def _get_kg_engine():
    """获取 KG 引擎实例。"""
    return _kg_engine_instance
