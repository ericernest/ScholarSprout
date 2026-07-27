"""渐进式知识图谱构建器。

按论文阅读进度渐进构建 KG 节点和边：

阅读进度 → KG 构建动作映射（来自 docs/reference/paper-reading/论文精读.docx 表10）:
  Abstract 读完      → Problem + Method(壳) 节点
  Introduction 读完   → Baseline节点集 + RelatedWork节点集 + inspires/extends 边
  Method §X 读完     → Module节点 + depends_on Concept 边 (核心创新→contributes_to)
  Experiment 读完    → Experiment + Dataset + Metric 节点集 + outperforms/ablates/evaluated_on 边
  Conclusion 读完    → Limitation + Claim 节点集 + contradicts 边
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from handlers.paper_reading.kg.engine import KnowledgeGraphEngine
from handlers.paper_reading.kg.models import (
    EDGE_TYPE_MAP,
    NODE_TYPE_MAP,
    KGEdge,
    KGNode,
)

logger = logging.getLogger(__name__)


@dataclass
class KGUpdateResult:
    """一次 KG 构建的结果。"""

    section_id: str = ""
    section_type: str = ""
    new_nodes: list[KGNode] = field(default_factory=list)
    new_edges: list[KGEdge] = field(default_factory=list)


class ProgressiveKGBuilder:
    """按阅读进度渐进式构建知识图谱。

    核心逻辑:
    1. 根据 section_id 判断章节类型 (abstract/intro/method/experiment/conclusion)
    2. 构造 LLM prompt 让模型从原文提取结构化 KG 元素
    3. 解析 LLM 输出（JSON），创建节点和边
    4. 与已有 KG 做冲突检测和合并
    """

    # 章节分类映射
    SECTION_KEYWORDS = {
        "abstract": ["abstract", "摘要", "概要"],
        "introduction": ["intro", "related", "background", "引言", "背景", "相关工作"],
        "method": ["method", "approach", "model", "framework", "architecture",
                    "方法", "模型", "框架", "架构", "方案"],
        "experiment": ["experiment", "evaluation", "result", "result and discussion",
                        "实验", "评估", "结果", "结果与讨论"],
        "conclusion": ["conclusion", "discussion", "future work", "limitation",
                        "总结", "讨论", "结论", "展望", "不足"],
    }

    def __init__(self, engine: KnowledgeGraphEngine) -> None:
        self.engine = engine
        self._built_papers: set[str] = set()

    # ── 整篇论文一次性构建 ──

    def build_full_paper(
        self,
        paper_id: str,
        paper_data: dict[str, Any],
        model: Any | None = None,
        force_rebuild: bool = False,
    ) -> KGUpdateResult:
        """一次性为整篇论文构建完整 KG。

        与旧的“读完一章构建一部分”不同，该方法在论文上传/首次阅读后就
        尽可能抽取完整的节点和边；阅读时只通过 ``get_revealed_subgraph()``
        按章节阶段渐进展示已经存在的 KG。
        """
        existing = self.engine.get_subgraph(paper_id)
        if existing.get("node_count", 0) and not force_rebuild:
            return KGUpdateResult(section_id="full_paper", section_type="full")

        sections = list(paper_data.get("sections", []) or [])
        extraction = self._extract_full_paper_kg_with_llm(paper_data, model)
        if not extraction.get("nodes"):
            extraction = self._extract_full_paper_kg_heuristic(paper_data)

        result = KGUpdateResult(section_id="full_paper", section_type="full")
        nodes, ref_map = self._materialize_nodes(paper_id, extraction.get("nodes", []))
        for node in nodes:
            self.engine.add_node(node)
            result.new_nodes.append(node)

        edges = self._materialize_edges(
            paper_id=paper_id,
            raw_edges=extraction.get("edges", []),
            ref_map=ref_map,
        )
        for edge in edges:
            try:
                self.engine.add_edge(edge.source_id, edge.target_id, edge)
                result.new_edges.append(edge)
            except Exception as error:
                logger.warning("Skip invalid KG edge %s: %s", edge.label, error)

        self._built_papers.add(paper_id)
        logger.info(
            "Full-paper KG built for %s: %d nodes, %d edges, %d sections",
            paper_id,
            len(result.new_nodes),
            len(result.new_edges),
            len(sections),
        )
        return result

    def ensure_full_paper_kg(
        self,
        paper_id: str,
        paper_data: dict[str, Any] | None,
        model: Any | None = None,
    ) -> KGUpdateResult:
        """确保指定论文已有完整 KG。"""
        if not paper_id or not paper_data:
            return KGUpdateResult(section_id="full_paper", section_type="missing")
        if self.engine.list_nodes_by_paper(paper_id):
            return KGUpdateResult(section_id="full_paper", section_type="existing")
        return self.build_full_paper(paper_id, paper_data, model=model)

    def get_revealed_subgraph(
        self,
        paper_id: str,
        current_section: str = "",
    ) -> dict[str, Any]:
        """按当前阅读阶段返回已展开的 KG 子图。

        完整 KG 已经提前构建；这里仅根据节点/边的 ``read_stage`` 控制前端
        看到多少内容。
        """
        current_stage = self.classify_section(current_section or "abstract")
        allowed = set(self._allowed_stages_until(current_stage))
        graph = self.engine.get_subgraph(paper_id)
        nodes = [
            node
            for node in graph.get("nodes", [])
            if node.get("properties", {}).get("read_stage", "general") in allowed
        ]
        node_ids = {node.get("node_id", "") for node in nodes}
        edges = [
            edge
            for edge in graph.get("edges", [])
            if edge.get("source") in node_ids
            and edge.get("target") in node_ids
            and edge.get("properties", {}).get("read_stage", "general") in allowed
        ]
        elements = self._to_cytoscape_elements(nodes, edges)
        return {
            "paper_id": paper_id,
            "current_stage": current_stage,
            "allowed_stages": list(allowed),
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "cytoscape_elements": elements,
        }

    @staticmethod
    def _allowed_stages_until(stage: str) -> list[str]:
        order = ["abstract", "introduction", "method", "experiment", "conclusion", "general"]
        if stage not in order:
            stage = "general"
        index = order.index(stage)
        return order[: index + 1]

    @staticmethod
    def _to_cytoscape_elements(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        type_colors = {
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
        elements: list[dict[str, Any]] = []
        for node in nodes:
            node_type = node.get("node_type", "Concept")
            elements.append({
                "data": {
                    "id": node.get("node_id", ""),
                    "label": node.get("label", ""),
                    "node_type": node_type,
                    "paper_id": node.get("paper_id", ""),
                    "section_id": node.get("section_id", ""),
                    "properties": node.get("properties", {}),
                    "color": type_colors.get(node_type, "#CCCCCC"),
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

    def _extract_full_paper_kg_with_llm(
        self,
        paper_data: dict[str, Any],
        model: Any | None,
    ) -> dict[str, Any]:
        if model is None:
            return {"nodes": [], "edges": []}
        prompt = self.build_full_paper_extraction_prompt(paper_data)
        try:
            response = model.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "你是论文知识图谱抽取器，只输出 JSON object。",
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            message = response.choices[0].message
            return self.parse_extraction_response(message.content or "")
        except Exception as error:
            logger.warning("Full-paper KG LLM extraction failed: %s", error)
            return {"nodes": [], "edges": []}

    def build_full_paper_extraction_prompt(self, paper_data: dict[str, Any]) -> str:
        sections = paper_data.get("sections", []) or []
        section_blocks = []
        for section in sections[:12]:
            title = section.get("title", "")
            content = section.get("content", "")
            section_blocks.append(
                f"[{section.get('section_id', '')}] {title}\n{content[:2500]}"
            )
        abstract = paper_data.get("abstract", "")
        references = paper_data.get("references", []) or []
        reference_text = "\n".join(
            f"- {ref.get('title', '')} ({ref.get('year', '')})"
            for ref in references[:20]
        )
        return (
            "请基于整篇论文一次性抽取完整知识图谱。不要按章节分批构建。\n"
            "节点类型只能是: Problem, Method, Module, Baseline, Metric, Dataset, "
            "Experiment, Figure, Concept, Limitation, Claim, RelatedWork, Insight。\n"
            "边类型只能是: motivates, extends, outperforms, depends_on, contradicts, "
            "ablates, inspired_by, evaluated_on, contributes_to。\n"
            "必须返回 JSON: {\"nodes\": [...], \"edges\": [...]}。\n"
            "node 字段: ref, node_type, label, section_id, read_stage, properties, confidence。\n"
            "edge 字段: source_ref, target_ref, edge_type, label, read_stage, properties, confidence。\n"
            "read_stage 只能是 abstract/introduction/method/experiment/conclusion/general，"
            "用于前端阅读时渐进展开。\n\n"
            f"标题: {paper_data.get('title', '')}\n"
            f"摘要: {abstract[:3000]}\n\n"
            f"章节:\n{chr(10).join(section_blocks)}\n\n"
            f"参考文献摘录:\n{reference_text}"
        )

    def _extract_full_paper_kg_heuristic(
        self,
        paper_data: dict[str, Any],
    ) -> dict[str, Any]:
        """无 LLM 时的保守 KG 兜底抽取。"""
        title = paper_data.get("title", "") or "Uploaded Paper"
        abstract = paper_data.get("abstract", "") or paper_data.get("full_text", "")[:1200]
        sections = list(paper_data.get("sections", []) or [])
        nodes: list[dict[str, Any]] = [
            {
                "ref": "problem:main",
                "node_type": "Problem",
                "label": self._short_label(abstract, "Research Problem"),
                "section_id": self._first_section_id(sections, "abstract"),
                "read_stage": "abstract",
                "properties": {"description": abstract[:800], "source": "heuristic"},
                "confidence": 0.55,
            },
            {
                "ref": "method:main",
                "node_type": "Method",
                "label": title[:120],
                "section_id": self._first_section_id(sections, "abstract"),
                "read_stage": "abstract",
                "properties": {
                    "name": title,
                    "description": abstract[:800],
                    "is_proposed": True,
                    "source": "heuristic",
                },
                "confidence": 0.6,
            },
        ]
        edges: list[dict[str, Any]] = [
            {
                "source_ref": "problem:main",
                "target_ref": "method:main",
                "edge_type": "motivates",
                "label": "研究问题驱动论文方法",
                "read_stage": "abstract",
                "properties": {"source": "heuristic"},
                "confidence": 0.55,
            }
        ]

        method_sections = [s for s in sections if self.classify_section(s.get("title") or s.get("section_id", "")) == "method"]
        for index, section in enumerate(method_sections[:8], start=1):
            ref = f"module:{index}"
            nodes.append({
                "ref": ref,
                "node_type": "Module",
                "label": self._clean_title(section.get("title", f"Module {index}")),
                "section_id": section.get("section_id", ""),
                "read_stage": "method",
                "properties": {
                    "description": (section.get("content") or "")[:600],
                    "is_contribution": index == 1,
                    "source": "heuristic",
                },
                "confidence": 0.5,
            })
            edges.append({
                "source_ref": ref,
                "target_ref": "method:main",
                "edge_type": "contributes_to",
                "label": "方法模块支撑整体方法",
                "read_stage": "method",
                "properties": {
                    "contribution_type": "core_innovation" if index == 1 else "supporting",
                    "source": "heuristic",
                },
                "confidence": 0.5,
            })

        for index, keyword in enumerate(self._extract_keywords(abstract), start=1):
            ref = f"concept:{index}"
            nodes.append({
                "ref": ref,
                "node_type": "Concept",
                "label": keyword,
                "section_id": self._first_section_id(sections, "abstract"),
                "read_stage": "abstract",
                "properties": {"name": keyword, "source": "heuristic"},
                "confidence": 0.45,
            })
            if index <= 5:
                edges.append({
                    "source_ref": "method:main",
                    "target_ref": ref,
                    "edge_type": "depends_on",
                    "label": "方法依赖核心概念",
                    "read_stage": "method",
                    "properties": {"source": "heuristic"},
                    "confidence": 0.45,
                })

        for index, ref_data in enumerate((paper_data.get("references", []) or [])[:8], start=1):
            title_text = ref_data.get("title", "") if isinstance(ref_data, dict) else ""
            if not title_text:
                continue
            ref = f"related:{index}"
            nodes.append({
                "ref": ref,
                "node_type": "RelatedWork",
                "label": title_text[:120],
                "section_id": self._first_section_id(sections, "introduction"),
                "read_stage": "introduction",
                "properties": {"paper_title": title_text, "relationship": "related", "source": "heuristic"},
                "confidence": 0.45,
            })
            edges.append({
                "source_ref": ref,
                "target_ref": "method:main",
                "edge_type": "inspired_by",
                "label": "相关工作为方法提供背景",
                "read_stage": "introduction",
                "properties": {"source": "heuristic"},
                "confidence": 0.4,
            })

        experiment_sections = [s for s in sections if self.classify_section(s.get("title") or s.get("section_id", "")) == "experiment"]
        experiment_text = "\n".join((s.get("content") or "")[:1200] for s in experiment_sections)
        for index, name in enumerate(self._extract_dataset_like_names(experiment_text), start=1):
            ref = f"dataset:{index}"
            nodes.append({
                "ref": ref,
                "node_type": "Dataset",
                "label": name,
                "section_id": self._first_section_id(experiment_sections, "experiment"),
                "read_stage": "experiment",
                "properties": {"name": name, "source": "heuristic"},
                "confidence": 0.45,
            })
            exp_ref = f"experiment:{index}"
            nodes.append({
                "ref": exp_ref,
                "node_type": "Experiment",
                "label": f"Experiment on {name}",
                "section_id": self._first_section_id(experiment_sections, "experiment"),
                "read_stage": "experiment",
                "properties": {"setting": {"dataset": name}, "source": "heuristic"},
                "confidence": 0.45,
            })
            edges.append({
                "source_ref": exp_ref,
                "target_ref": ref,
                "edge_type": "evaluated_on",
                "label": "实验在数据集上评测",
                "read_stage": "experiment",
                "properties": {"source": "heuristic"},
                "confidence": 0.45,
            })

        conclusion = self._first_section_text(sections, "conclusion")
        if conclusion:
            nodes.append({
                "ref": "claim:main",
                "node_type": "Claim",
                "label": self._short_label(conclusion, "Main Claim"),
                "section_id": self._first_section_id(sections, "conclusion"),
                "read_stage": "conclusion",
                "properties": {"statement": conclusion[:700], "source": "heuristic"},
                "confidence": 0.45,
            })
            edges.append({
                "source_ref": "method:main",
                "target_ref": "claim:main",
                "edge_type": "contributes_to",
                "label": "方法支撑论文主张",
                "read_stage": "conclusion",
                "properties": {"source": "heuristic"},
                "confidence": 0.45,
            })

        return {"nodes": nodes, "edges": edges}

    def _materialize_nodes(
        self,
        paper_id: str,
        raw_nodes: list[dict[str, Any]],
    ) -> tuple[list[KGNode], dict[str, str]]:
        nodes: list[KGNode] = []
        ref_map: dict[str, str] = {}
        seen: dict[tuple[str, str], str] = {}
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                continue
            node_type = str(raw.get("node_type", "Concept"))
            if node_type not in NODE_TYPE_MAP:
                node_type = "Concept"
            label = str(raw.get("label") or raw.get("name") or node_type).strip()[:180]
            if not label:
                continue
            key = (node_type, label.lower())
            ref = str(raw.get("ref") or f"{node_type}:{label}")
            if key in seen:
                ref_map[ref] = seen[key]
                continue
            properties = dict(raw.get("properties") or {})
            properties.setdefault("read_stage", self._normalize_stage(raw.get("read_stage", "general")))
            properties.setdefault("source_ref", ref)
            node_cls = NODE_TYPE_MAP[node_type]
            node = node_cls(
                node_id=str(uuid4()),
                node_type=node_type,
                label=label,
                paper_id=paper_id,
                section_id=str(raw.get("section_id", "")),
                properties=properties,
                confidence=float(raw.get("confidence", 0.7) or 0.7),
            )
            nodes.append(node)
            ref_map[ref] = node.node_id
            seen[key] = node.node_id
        return nodes, ref_map

    def _materialize_edges(
        self,
        paper_id: str,
        raw_edges: list[dict[str, Any]],
        ref_map: dict[str, str],
    ) -> list[KGEdge]:
        edges: list[KGEdge] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            edge_type = str(raw.get("edge_type", "depends_on"))
            if edge_type not in EDGE_TYPE_MAP:
                continue
            source_id = ref_map.get(str(raw.get("source_ref", ""))) or str(raw.get("source_id", ""))
            target_id = ref_map.get(str(raw.get("target_ref", ""))) or str(raw.get("target_id", ""))
            if not source_id or not target_id or source_id == target_id:
                continue
            key = (source_id, target_id, edge_type)
            if key in seen:
                continue
            seen.add(key)
            properties = dict(raw.get("properties") or {})
            properties.setdefault("read_stage", self._normalize_stage(raw.get("read_stage", "general")))
            edge_cls = EDGE_TYPE_MAP[edge_type]
            edges.append(edge_cls(
                source_id=source_id,
                target_id=target_id,
                edge_type=edge_type,
                label=str(raw.get("label", edge_type)),
                paper_id=paper_id,
                properties=properties,
                confidence=float(raw.get("confidence", 0.7) or 0.7),
            ))
        return edges

    @staticmethod
    def _normalize_stage(stage: Any) -> str:
        value = str(stage or "general").lower()
        return value if value in {"abstract", "introduction", "method", "experiment", "conclusion", "general"} else "general"

    @staticmethod
    def _clean_title(title: str) -> str:
        return re.sub(r"^\s*\d+(\.\d+)*\s*", "", title).strip()[:120] or "Untitled"

    @staticmethod
    def _short_label(text: str, fallback: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if not cleaned:
            return fallback
        sentence = re.split(r"[。.!?]", cleaned)[0].strip()
        return sentence[:120] or fallback

    def _first_section_id(self, sections: list[dict[str, Any]], stage: str) -> str:
        for section in sections:
            if self.classify_section(section.get("title") or section.get("section_id", "")) == stage:
                return section.get("section_id", "")
        return sections[0].get("section_id", "") if sections else ""

    def _first_section_text(self, sections: list[dict[str, Any]], stage: str) -> str:
        for section in sections:
            if self.classify_section(section.get("title") or section.get("section_id", "")) == stage:
                return section.get("content", "")
        return ""

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        candidates = re.findall(r"\b[A-Z][A-Za-z0-9-]{3,}\b", text or "")
        stop = {"This", "That", "These", "Using", "Based", "However", "Abstract"}
        unique: list[str] = []
        for word in candidates:
            if word in stop or word in unique:
                continue
            unique.append(word)
            if len(unique) >= 8:
                break
        return unique

    @staticmethod
    def _extract_dataset_like_names(text: str) -> list[str]:
        patterns = [
            r"\b[A-Za-z]+(?:ImageNet|MNIST|CIFAR|SQuAD|GLUE|COCO|WikiText)[A-Za-z0-9-]*\b",
            r"\b(?:ImageNet|MNIST|CIFAR-?\d*|SQuAD|GLUE|SuperGLUE|COCO|WikiText-?\d*)\b",
        ]
        names: list[str] = []
        for pattern in patterns:
            for match in re.findall(pattern, text or ""):
                if match not in names:
                    names.append(match)
                if len(names) >= 6:
                    return names
        return names

    # ── 分类 ──

    def classify_section(self, section_id: str) -> str:
        """将 section_id 归类到构建阶段。"""
        sid = section_id.lower()
        for category, keywords in self.SECTION_KEYWORDS.items():
            for kw in keywords:
                if kw in sid:
                    return category
        return "general"

    # ── 构建入口 ──

    def build_for_section(
        self,
        paper_id: str,
        section_id: str,
        section_text: str,
        existing_kg: dict | None = None,
    ) -> KGUpdateResult:
        """为指定章节构建 KG 节点和边。

        Args:
            paper_id: 论文 ID
            section_id: 章节标识（用于分类）
            section_text: 章节原文
            existing_kg: 已有 KG 快照（用于上下文感知）

        Returns:
            KGUpdateResult 包含新增的节点和边
        """
        section_type = self.classify_section(section_id)
        result = KGUpdateResult(section_id=section_id, section_type=section_type)

        logger.info(
            "Building KG for %s (type: %s, text_len: %d)",
            section_id, section_type, len(section_text),
        )

        # 根据章节类型调用对应的构建方法
        builder_methods = {
            "abstract": self._build_abstract_kg,
            "introduction": self._build_introduction_kg,
            "method": self._build_method_kg,
            "experiment": self._build_experiment_kg,
            "conclusion": self._build_conclusion_kg,
            "general": self._build_general_kg,
        }

        builder = builder_methods.get(section_type, self._build_general_kg)
        nodes, edges = builder(paper_id, section_id, section_text, existing_kg)

        # 添加到引擎
        for node in nodes:
            try:
                self.engine.add_node(node)
                result.new_nodes.append(node)
            except Exception as e:
                logger.error("Failed to add node: %s", e)

        for edge in edges:
            try:
                self.engine.add_edge(edge.source_id, edge.target_id, edge)
                result.new_edges.append(edge)
            except Exception as e:
                logger.error("Failed to add edge: %s", e)

        return result

    # ── 各章节构建方法 ──

    def _build_abstract_kg(
        self, paper_id: str, section_id: str, text: str, existing_kg: dict | None
    ) -> tuple[list[KGNode], list[KGEdge]]:
        """Abstract 读取后: 创建 Problem + Method(壳) 节点。"""
        nodes = []
        nodes.append(KGNode(
            node_type="Problem",
            label="Research Problem",
            paper_id=paper_id,
            section_id=section_id,
            properties={"description": text[:500]},
        ))
        nodes.append(KGNode(
            node_type="Method",
            label="Proposed Method",
            paper_id=paper_id,
            section_id=section_id,
            properties={"description": text[:500], "is_proposed": True},
        ))
        return nodes, []

    def _build_introduction_kg(
        self, paper_id: str, section_id: str, text: str, existing_kg: dict | None
    ) -> tuple[list[KGNode], list[KGEdge]]:
        """Introduction 读取后: 创建 Baseline + RelatedWork + 关联边。"""
        nodes = []
        edges = []
        # 占位: 实际实现中由 LLM 提取，此处提供基本模板
        return nodes, edges

    def _build_method_kg(
        self, paper_id: str, section_id: str, text: str, existing_kg: dict | None
    ) -> tuple[list[KGNode], list[KGEdge]]:
        """Method 章节读取后: 创建 Module + depends_on + contributes_to 边。"""
        nodes = []
        edges = []
        return nodes, edges

    def _build_experiment_kg(
        self, paper_id: str, section_id: str, text: str, existing_kg: dict | None
    ) -> tuple[list[KGNode], list[KGEdge]]:
        """Experiment 读取后: 创建 Experiment + Dataset + Metric + outperforms/ablates/evaluated_on。"""
        nodes = []
        edges = []
        return nodes, edges

    def _build_conclusion_kg(
        self, paper_id: str, section_id: str, text: str, existing_kg: dict | None
    ) -> tuple[list[KGNode], list[KGEdge]]:
        """Conclusion 读取后: 创建 Limitation + Claim + contradicts。"""
        nodes = []
        edges = []
        return nodes, edges

    def _build_general_kg(
        self, paper_id: str, section_id: str, text: str, existing_kg: dict | None
    ) -> tuple[list[KGNode], list[KGEdge]]:
        """通用章节: 创建 Concept 节点。"""
        return [], []

    # ── LLM 辅助方法 ──

    def build_extraction_prompt(
        self,
        section_type: str,
        section_text: str,
        existing_kg: dict | None,
    ) -> str:
        """为不同章节类型构建专用的 KG 提取 prompt。

        该 prompt 会被注入到 agent 的 system_prompt 中，
        或者作为独立的 LLM 调用使用。
        """
        prompts = {
            "abstract": (
                "从以下论文摘要中提取关键信息并构建知识图谱元素：\n\n"
                "1. 识别研究问题（Problem）: 一句话描述\n"
                "2. 识别提出的方法（Method）: 方法名称、类别\n\n"
                "返回 JSON: {nodes: [{node_type, label, properties}], edges: []}\n\n"
                f"摘要文本:\n{section_text[:3000]}"
            ),
            "introduction": (
                "从以下论文引言中提取相关知识图谱元素：\n\n"
                "1. 识别对比基线（Baseline）: 名称、来源论文、类别\n"
                "2. 识别相关工作（RelatedWork）: 论文标题、关系类型(precursor/contemporary/alternative)\n"
                "3. 创建关系边: inspires, extends\n\n"
                "返回 JSON: {nodes: [...], edges: [...]}\n\n"
                f"引言文本:\n{section_text[:5000]}"
            ),
            "method": (
                "从以下论文方法章节中提取知识图谱元素：\n\n"
                "1. 识别方法子模块（Module）: 名称、父方法、是否为核心创新\n"
                "2. 识别依赖概念（Concept）: 名称、定义\n"
                "3. 创建关系边: depends_on, contributes_to (标注 contribution_type)\n\n"
                "返回 JSON: {nodes: [...], edges: [...]}\n\n"
                f"方法文本:\n{section_text[:8000]}"
            ),
            "experiment": (
                "从以下实验章节中提取知识图谱元素：\n\n"
                "1. 识别实验（Experiment）: 实验设置、结果 JSON\n"
                "2. 识别数据集（Dataset）: 名称、规模\n"
                "3. 识别评估指标（Metric）: 名称、越高越好?\n"
                "4. 创建关系边: outperforms (含 metric/margin), ablates (含 effect), evaluated_on\n\n"
                "返回 JSON: {nodes: [...], edges: [...]}\n\n"
                f"实验文本:\n{section_text[:8000]}"
            ),
            "conclusion": (
                "从以下论文结论中提取知识图谱元素：\n\n"
                "1. 识别局限性（Limitation）: 描述、严重程度(1-5)\n"
                "2. 识别论文声明（Claim）: 声明内容、证据级别(experimental/theoretical/anecdotal)\n"
                "3. 创建关系边: contradicts (如有矛盾声明)\n\n"
                "返回 JSON: {nodes: [...], edges: [...]}\n\n"
                f"结论文本:\n{section_text[:3000]}"
            ),
        }
        return prompts.get(section_type, f"从以下文本中提取知识图谱元素：\n\n{section_text[:5000]}")

    @staticmethod
    def parse_extraction_response(response_text: str) -> dict[str, Any]:
        """从 LLM 响应中解析 KG 提取结果。"""
        # 尝试直接 JSON 解析
        text = response_text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从 Markdown 代码块中提取
        import re
        json_match = re.search(
            r"```(?:json)?\s*([\s\S]*?)\s*```",
            text,
        )
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试找到 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        return {"nodes": [], "edges": []}
