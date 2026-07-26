"""KG 驱动问答引擎。

该模块把自然语言问题映射到论文知识图谱查询，再返回前端可视化需要的
自然语言答案、推理路径、证据节点和 Cytoscape elements。
"""

from __future__ import annotations

import json
import re
from typing import Any

from paper_reading.kg.engine import KnowledgeGraphEngine


class KGQueryEngine:
    """基于 KG 的论文问答。"""

    def __init__(self, engine: KnowledgeGraphEngine, model: Any | None = None) -> None:
        self.engine = engine
        self.model = model

    def answer(
        self,
        question: str,
        paper_id: str = "",
        query_type: str = "auto",
        source_label: str = "",
        target_label: str = "",
        node_id: str = "",
    ) -> dict[str, Any]:
        """回答一个 KG 问题。"""
        question = question.strip()
        if not question:
            return {
                "question": "",
                "answer": "请提供一个要查询的问题。",
                "reasoning_paths": [],
                "evidence": [],
                "matched_nodes": [],
                "cytoscape_elements": [],
            }

        graph = self.engine.get_subgraph(paper_id) if paper_id else self.engine.to_dict()
        matched_nodes = self._match_nodes(question, graph.get("nodes", []))
        reasoning_paths = self._collect_reasoning_paths(
            question=question,
            graph=graph,
            matched_nodes=matched_nodes,
            query_type=query_type,
            source_label=source_label,
            target_label=target_label,
            node_id=node_id,
        )
        evidence = self._collect_evidence(reasoning_paths, matched_nodes)
        answer = self._compose_answer(question, matched_nodes, reasoning_paths, evidence)
        cyto = self._subgraph_to_cytoscape(reasoning_paths, matched_nodes, paper_id)

        return {
            "question": question,
            "answer": answer,
            "reasoning_paths": reasoning_paths,
            "evidence": evidence,
            "matched_nodes": matched_nodes[:8],
            "cytoscape_elements": cyto,
        }

    def _match_nodes(
        self,
        question: str,
        nodes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized_question = question.lower()
        tokens = self._question_tokens(question)
        scored: list[tuple[int, dict[str, Any]]] = []
        for node in nodes:
            label = str(node.get("label", ""))
            properties = node.get("properties", {}) or {}
            description = str(properties.get("description") or properties.get("statement") or "")
            haystack = f"{label} {description}".lower()
            score = 0
            if label and label.lower() in normalized_question:
                score += 8
            for token in tokens:
                if token in haystack:
                    score += 1
            if score:
                scored.append((score, node))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [node for _, node in scored[:10]]

    def _collect_reasoning_paths(
        self,
        question: str,
        graph: dict[str, Any],
        matched_nodes: list[dict[str, Any]],
        query_type: str,
        source_label: str,
        target_label: str,
        node_id: str,
    ) -> list[dict[str, Any]]:
        if query_type == "path" and source_label and target_label:
            return self.engine.query_path(source_label, target_label)
        if query_type == "neighbors" and node_id:
            return [self.engine.get_neighbors(node_id, depth=2)]

        node_ids = {node.get("node_id", "") for node in matched_nodes[:5]}
        if not node_ids:
            node_ids = {
                node.get("node_id", "")
                for node in graph.get("nodes", [])[:3]
            }

        paths: list[dict[str, Any]] = []
        node_lookup = {node.get("node_id", ""): node for node in graph.get("nodes", [])}
        for edge in graph.get("edges", []):
            source = edge.get("source", "")
            target = edge.get("target", "")
            if source not in node_ids and target not in node_ids:
                continue
            source_node = node_lookup.get(source, {})
            target_node = node_lookup.get(target, {})
            paths.append({
                "source_id": source,
                "source_label": source_node.get("label", ""),
                "source_type": source_node.get("node_type", ""),
                "relation": edge.get("edge_type", ""),
                "relation_label": edge.get("label", ""),
                "target_id": target,
                "target_label": target_node.get("label", ""),
                "target_type": target_node.get("node_type", ""),
                "properties": edge.get("properties", {}),
                "paper_id": edge.get("paper_id", ""),
            })
            if len(paths) >= 12:
                break
        return paths

    def _collect_evidence(
        self,
        reasoning_paths: list[dict[str, Any]],
        matched_nodes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in matched_nodes:
            node_id = node.get("node_id", "")
            if node_id and node_id not in seen:
                evidence.append(self._node_evidence(node))
                seen.add(node_id)
        for path in reasoning_paths:
            for prefix in ("source", "target"):
                node_id = path.get(f"{prefix}_id", "")
                if not node_id or node_id in seen:
                    continue
                node = self.engine.get_node(node_id) or {}
                node["node_id"] = node_id
                evidence.append(self._node_evidence(node))
                seen.add(node_id)
        return evidence[:12]

    @staticmethod
    def _node_evidence(node: dict[str, Any]) -> dict[str, Any]:
        properties = node.get("properties", {}) or {}
        return {
            "node_id": node.get("node_id", ""),
            "label": node.get("label", ""),
            "node_type": node.get("node_type", ""),
            "section_id": node.get("section_id", ""),
            "summary": (
                properties.get("description")
                or properties.get("statement")
                or properties.get("name")
                or ""
            )[:500],
            "properties": properties,
        }

    def _compose_answer(
        self,
        question: str,
        matched_nodes: list[dict[str, Any]],
        reasoning_paths: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> str:
        if self.model is not None and (reasoning_paths or evidence):
            prompt = {
                "question": question,
                "matched_nodes": matched_nodes[:6],
                "reasoning_paths": reasoning_paths[:10],
                "evidence": evidence[:8],
            }
            try:
                response = self.model.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是论文精读问答助手。只基于给定 KG 路径和证据回答，"
                                "不要编造 KG 中不存在的论文事实。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(prompt, ensure_ascii=False),
                        },
                    ]
                )
                return response.choices[0].message.content or ""
            except Exception:
                pass

        if not matched_nodes and not reasoning_paths:
            return "当前知识图谱中没有找到足够证据回答这个问题。建议先完成论文解析和 KG 构建。"

        lines = ["基于当前知识图谱，可以这样理解："]
        if matched_nodes:
            labels = "、".join(node.get("label", "") for node in matched_nodes[:4])
            lines.append(f"- 相关节点: {labels}")
        if reasoning_paths:
            lines.append("- 关键关系:")
            for path in reasoning_paths[:5]:
                lines.append(
                    f"  {path.get('source_label', '')} --{path.get('relation', '')}--> "
                    f"{path.get('target_label', '')}"
                )
        if evidence:
            section_ids = [item.get("section_id", "") for item in evidence if item.get("section_id")]
            if section_ids:
                lines.append(f"- 证据位置: {', '.join(section_ids[:5])}")
        return "\n".join(lines)

    def _subgraph_to_cytoscape(
        self,
        reasoning_paths: list[dict[str, Any]],
        matched_nodes: list[dict[str, Any]],
        paper_id: str,
    ) -> list[dict[str, Any]]:
        ids = {node.get("node_id", "") for node in matched_nodes}
        for path in reasoning_paths:
            ids.add(path.get("source_id", ""))
            ids.add(path.get("target_id", ""))
        ids.discard("")
        graph = self.engine.get_subgraph(paper_id) if paper_id else self.engine.to_dict()
        nodes = [node for node in graph.get("nodes", []) if node.get("node_id") in ids]
        edges = [
            edge
            for edge in graph.get("edges", [])
            if edge.get("source") in ids and edge.get("target") in ids
        ]
        return self._to_cytoscape(nodes, edges)

    @staticmethod
    def _to_cytoscape(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        for node in nodes:
            elements.append({
                "data": {
                    "id": node.get("node_id", ""),
                    "label": node.get("label", ""),
                    "node_type": node.get("node_type", ""),
                    "paper_id": node.get("paper_id", ""),
                    "section_id": node.get("section_id", ""),
                    "properties": node.get("properties", {}),
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
        return elements

    @staticmethod
    def _question_tokens(question: str) -> list[str]:
        raw = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", question)
        stop = {"什么", "为什么", "如何", "哪些", "这个", "论文", "方法", "模型", "关系"}
        return [item.lower() for item in raw if item.lower() not in stop][:20]
