"""论文知识图谱引擎。

基于 networkx.DiGraph，支持:
- 13 种节点的 CRUD
- 9 种边的 CRUD
- 图遍历查询、子图导出、路径查找
- Cytoscape.js 格式导出（前端可视化）
- 跨论文图融合
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import networkx as nx

from paper_reading.kg.models import (
    ALL_EDGE_TYPES,
    ALL_NODE_TYPES,
    KGEdge,
    KGNode,
    NODE_TYPE_MAP,
    EDGE_TYPE_MAP,
)

logger = logging.getLogger(__name__)


class KnowledgeGraphEngine:
    """论文知识图谱引擎。

    设计要点:
    - 使用 networkx.DiGraph 进行图存储和算法运算
    - networkx 节点属性存储 KGNode 的完整数据
    - networkx 边属性存储 KGEdge 的完整数据
    - 支持按 paper_id 分片和跨论文遍历
    """

    def __init__(self) -> None:
        self._graph = nx.DiGraph()
        self._node_counter = 0
        self._edge_counter = 0

    # ── 节点操作 ──

    def add_node(self, node: KGNode) -> str:
        """添加节点到图谱。

        Args:
            node: KGNode 子类实例

        Returns:
            节点 node_id
        """
        if not node.node_id:
            node.node_id = str(uuid4())
        if not node.created_at:
            node.created_at = datetime.now(timezone.utc).isoformat()

        self._graph.add_node(
            node.node_id,
            node_type=node.node_type,
            label=node.label,
            paper_id=node.paper_id,
            section_id=node.section_id,
            properties=node.properties,
            confidence=node.confidence,
            created_at=node.created_at,
        )
        self._node_counter += 1
        return node.node_id

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """获取节点数据。"""
        if node_id not in self._graph:
            return None
        return dict(self._graph.nodes[node_id])

    def update_node(self, node_id: str, properties: dict[str, Any]) -> bool:
        """更新节点属性（合并模式）。"""
        if node_id not in self._graph:
            return False
        current = self._graph.nodes[node_id].get("properties", {})
        current.update(properties)
        self._graph.nodes[node_id]["properties"] = current
        return True

    def remove_node(self, node_id: str) -> bool:
        """移除节点及其关联的所有边。"""
        if node_id not in self._graph:
            return False
        self._graph.remove_node(node_id)
        return True

    def list_nodes_by_type(self, node_type: str) -> list[dict[str, Any]]:
        """按节点类型过滤。"""
        if node_type not in ALL_NODE_TYPES:
            return []
        return [
            {"node_id": n, **dict(self._graph.nodes[n])}
            for n in self._graph.nodes
            if self._graph.nodes[n].get("node_type") == node_type
        ]

    def list_nodes_by_paper(self, paper_id: str) -> list[dict[str, Any]]:
        """按论文 ID 过滤节点。"""
        return [
            {"node_id": n, **dict(self._graph.nodes[n])}
            for n in self._graph.nodes
            if self._graph.nodes[n].get("paper_id") == paper_id
        ]

    def search_nodes(self, keyword: str, paper_id: str = "") -> list[dict[str, Any]]:
        """按关键词搜索节点（label 和 description 模糊匹配）。"""
        kw = keyword.lower()
        results = []
        for n in self._graph.nodes:
            data = dict(self._graph.nodes[n])
            if paper_id and data.get("paper_id") != paper_id:
                continue
            label = (data.get("label", "") or "").lower()
            desc = data.get("properties", {}).get("description", "").lower()
            if kw in label or kw in desc:
                results.append({"node_id": n, **data})
        return results

    # ── 边操作 ──

    def add_edge(self, source_id: str, target_id: str, edge: KGEdge) -> str:
        """添加边到图谱。

        Args:
            source_id: 源节点 node_id
            target_id: 目标节点 node_id
            edge: KGEdge 子类实例

        Returns:
            边 edge_id
        """
        if not self._graph.has_node(source_id):
            raise ValueError(f"Source node not found: {source_id}")
        if not self._graph.has_node(target_id):
            raise ValueError(f"Target node not found: {target_id}")

        if not edge.edge_id:
            edge.edge_id = str(uuid4())
        if not edge.created_at:
            edge.created_at = datetime.now(timezone.utc).isoformat()

        self._graph.add_edge(
            source_id,
            target_id,
            edge_id=edge.edge_id,
            edge_type=edge.edge_type,
            label=edge.label,
            paper_id=edge.paper_id,
            properties=edge.properties,
            confidence=edge.confidence,
            created_at=edge.created_at,
        )
        self._edge_counter += 1
        return edge.edge_id

    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        """按 edge_id 查找边。"""
        for u, v, data in self._graph.edges(data=True):
            if data.get("edge_id") == edge_id:
                return {"source": u, "target": v, **data}
        return None

    def remove_edge(self, source_id: str, target_id: str) -> bool:
        """移除指定边。"""
        if not self._graph.has_edge(source_id, target_id):
            return False
        self._graph.remove_edge(source_id, target_id)
        return True

    def list_edges_by_type(self, edge_type: str) -> list[dict[str, Any]]:
        """按边类型过滤。"""
        if edge_type not in ALL_EDGE_TYPES:
            return []
        return [
            {"source": u, "target": v, **data}
            for u, v, data in self._graph.edges(data=True)
            if data.get("edge_type") == edge_type
        ]

    def list_edges_by_paper(self, paper_id: str) -> list[dict[str, Any]]:
        """按论文 ID 过滤边。"""
        return [
            {"source": u, "target": v, **data}
            for u, v, data in self._graph.edges(data=True)
            if data.get("paper_id") == paper_id
        ]

    # ── 图查询 ──

    def get_subgraph(self, paper_id: str) -> dict[str, Any]:
        """获取指定论文的完整子图。"""
        nodes = self.list_nodes_by_paper(paper_id)
        edges = self.list_edges_by_paper(paper_id)
        return {
            "paper_id": paper_id,
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def get_neighbors(self, node_id: str, depth: int = 1) -> dict[str, Any]:
        """获取节点的邻域子图。

        Args:
            node_id: 中心节点
            depth: 扩展深度（默认 1 表示直接邻居）

        Returns:
            包含中心节点、入边、出边或深度子图的数据。
        """
        if node_id not in self._graph:
            return {"error": f"Node not found: {node_id}", "node": None}

        if depth == 1:
            predecessors = list(self._graph.predecessors(node_id))
            successors = list(self._graph.successors(node_id))
            node_data = self.get_node(node_id)
            incoming = []
            for p in predecessors:
                edge_data = dict(self._graph.edges[p, node_id])
                incoming.append({"from_node": p, "from_label": self._graph.nodes[p].get("label", ""), "edge": edge_data})
            outgoing = []
            for s in successors:
                edge_data = dict(self._graph.edges[node_id, s])
                outgoing.append({"to_node": s, "to_label": self._graph.nodes[s].get("label", ""), "edge": edge_data})
            return {
                "node": node_data,
                "incoming": incoming,
                "outgoing": outgoing,
            }

        # depth > 1: 使用 nx.ego_graph
        sub_nodes = nx.ego_graph(self._graph, node_id, radius=depth)
        return {
            "nodes": [{"node_id": n, **dict(self._graph.nodes[n])} for n in sub_nodes.nodes],
            "edges": [
                {"source": u, "target": v, **dict(sub_nodes.edges[u, v])}
                for u, v in sub_nodes.edges
            ],
        }

    def query_path(
        self, source_label: str, target_label: str
    ) -> list[dict[str, Any]]:
        """查找两个实体间的直接关联路径。

        通过 label 模糊匹配源和目标节点，返回所有连接边。
        用于 KG 驱动问答的核心查询。

        示例: query_path("ProtoNet", "MAML")
        → [outperforms edge with metric/margin/experiment]
        """
        results = []
        source_lower = source_label.lower()
        target_lower = target_label.lower()

        for u, v, data in self._graph.edges(data=True):
            u_label = (self._graph.nodes[u].get("label", "") or "").lower()
            v_label = (self._graph.nodes[v].get("label", "") or "").lower()
            if source_lower in u_label and target_lower in v_label:
                results.append({
                    "source_id": u,
                    "source_label": u_label,
                    "relation": data["edge_type"],
                    "relation_label": data.get("label", ""),
                    "target_id": v,
                    "target_label": v_label,
                    "properties": data.get("properties", {}),
                    "paper_id": data.get("paper_id", ""),
                })
        return results

    def find_path(
        self, source_id: str, target_id: str, max_hops: int = 3
    ) -> list[str]:
        """使用 networkx 最短路径算法查找两节点的最短路径。"""
        try:
            path = nx.shortest_path(self._graph, source=source_id, target=target_id)
            if len(path) - 1 <= max_hops:
                return path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass
        return []

    # ── 统计 ──

    @property
    def size(self) -> tuple[int, int]:
        """返回 (节点数, 边数)。"""
        return (self._graph.number_of_nodes(), self._graph.number_of_edges())

    def get_stats(self) -> dict[str, Any]:
        """获取图谱统计信息。"""
        paper_ids = set(
            self._graph.nodes[n].get("paper_id", "")
            for n in self._graph.nodes
        )
        return {
            "node_count": self._graph.number_of_nodes(),
            "edge_count": self._graph.number_of_edges(),
            "paper_count": len([p for p in paper_ids if p]),
            "node_type_distribution": {
                nt: len(self.list_nodes_by_type(nt))
                for nt in ALL_NODE_TYPES
            },
            "edge_type_distribution": {
                et: len(self.list_edges_by_type(et))
                for et in ALL_EDGE_TYPES
            },
        }

    # ── 序列化 ──

    def to_dict(self, paper_id: str = "") -> dict[str, Any]:
        """导出为字典格式。"""
        if paper_id:
            return self.get_subgraph(paper_id)

        nodes = [
            {"node_id": n, **dict(self._graph.nodes[n])}
            for n in self._graph.nodes
        ]
        edges = [
            {"source": u, "target": v, **dict(self._graph.edges[u, v])}
            for u, v in self._graph.edges
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        """从字典恢复图谱（增量追加模式）。"""
        for node_data in data.get("nodes", []):
            node_id = node_data.pop("node_id", str(uuid4()))
            self._graph.add_node(node_id, **node_data)
        for edge_data in data.get("edges", []):
            source = edge_data.pop("source", "")
            target = edge_data.pop("target", "")
            if source and target and self._graph.has_node(source) and self._graph.has_node(target):
                self._graph.add_edge(source, target, **edge_data)

    def to_cytoscape(self, paper_id: str = "") -> dict[str, Any]:
        """导出为 Cytoscape.js elements 数组格式（前端直接渲染）。

        Args:
            paper_id: 可选，限定论文范围

        Returns:
            {"elements": [...]} 格式的字典
        """
        if paper_id:
            subgraph = self.get_subgraph(paper_id)
            nodes = subgraph["nodes"]
            edges = subgraph["edges"]
        else:
            nodes = [
                {"node_id": n, **dict(self._graph.nodes[n])}
                for n in self._graph.nodes
            ]
            edges = [
                {"source": u, "target": v, **dict(self._graph.edges[u, v])}
                for u, v in self._graph.edges
            ]

        elements: list[dict[str, Any]] = []

        # 节点颜色映射
        type_colors: dict[str, str] = {
            "Problem": "#E74C3C",
            "Method": "#3498DB",
            "Module": "#2ECC71",
            "Baseline": "#95A5A6",
            "Metric": "#F39C12",
            "Dataset": "#9B59B6",
            "Experiment": "#1ABC9C",
            "Figure": "#E67E22",
            "Concept": "#16A085",
            "Limitation": "#E74C3C",
            "Claim": "#D35400",
            "RelatedWork": "#7F8C8D",
            "Insight": "#F1C40F",
        }

        for node in nodes:
            nt = node.get("node_type", "Concept")
            elements.append({
                "data": {
                    "id": node.get("node_id", node.get("id", "")),
                    "label": node.get("label", ""),
                    "node_type": nt,
                    "paper_id": node.get("paper_id", ""),
                    "properties": node.get("properties", {}),
                    "color": type_colors.get(nt, "#CCCCCC"),
                }
            })

        for edge in edges:
            elements.append({
                "data": {
                    "id": edge.get("edge_id", ""),
                    "source": edge.get("source", ""),
                    "target": edge.get("target", ""),
                    "edge_type": edge.get("edge_type", ""),
                    "label": edge.get("label", ""),
                    "properties": edge.get("properties", {}),
                }
            })

        return {"elements": elements}

    def clear(self) -> None:
        """清空图谱。"""
        self._graph.clear()
        self._node_counter = 0
        self._edge_counter = 0
