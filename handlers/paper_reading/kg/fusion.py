"""跨论文 KG 融合引擎。

触发条件（来自 docs/reference/paper-reading/论文精读.docx 5.3.4）:
1. 匹配同名 Dataset → 合并节点，标注实验设置差异
2. 匹配同名 Baseline → 创建关联，支持结果对比
3. 检测概念演化链 → auto extends 边 + key_difference
4. 检测矛盾结论 → auto contradicts 边 + 高亮
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from handlers.paper_reading.kg.engine import KnowledgeGraphEngine
from handlers.paper_reading.kg.models import KGEdge, KGNode

logger = logging.getLogger(__name__)


@dataclass
class FusionEvent:
    event_type: str = ""
    source: str = ""
    target: str = ""
    description: str = ""
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class FusionResult:
    paper_id_a: str = ""
    paper_id_b: str = ""
    events: list[FusionEvent] = field(default_factory=list)
    new_edges: list[KGEdge] = field(default_factory=list)
    merged_nodes: list[dict[str, Any]] = field(default_factory=list)


class CrossPaperFusion:
    """跨论文知识图谱融合引擎。

    在用户读完多篇论文后自动触发，
    检测共享节点、演化链和矛盾结论。
    """

    def __init__(self, engine: KnowledgeGraphEngine) -> None:
        self.engine = engine

    def fuse(self, paper_id_a: str, paper_id_b: str) -> FusionResult:
        """融合两篇论文的 KG。

        Args:
            paper_id_a: 论文 A 的 ID
            paper_id_b: 论文 B 的 ID

        Returns:
            FusionResult 包含所有融合事件和变更
        """
        result = FusionResult(paper_id_a=paper_id_a, paper_id_b=paper_id_b)

        # 1. 检测同名 Dataset/Baseline/Concept/Metric 节点并合并
        result.events.extend(self._fuse_shared_nodes(paper_id_a, paper_id_b, result))

        # 2. 检测 extends 演化链
        result.events.extend(self._detect_evolution(paper_id_a, paper_id_b, result))

        # 3. 检测 contradicts 矛盾
        result.events.extend(self._detect_contradictions(paper_id_a, paper_id_b, result))

        logger.info(
            "Fusion %s ↔ %s: %d events, %d edges, %d merged nodes",
            paper_id_a, paper_id_b,
            len(result.events), len(result.new_edges), len(result.merged_nodes),
        )
        return result

    # ── 共享节点检测 ──

    def _fuse_shared_nodes(
        self, pid_a: str, pid_b: str, result: FusionResult
    ) -> list[FusionEvent]:
        """匹配同名节点并记录融合事件。

        匹配逻辑:
        1. node_type 相同
        2. label 相同（大小写不敏感）
        3. 节点来自不同论文
        """
        events: list[FusionEvent] = []
        nodes_a = self.engine.list_nodes_by_paper(pid_a)
        nodes_b = self.engine.list_nodes_by_paper(pid_b)

        # 建立 b 的查表索引: (type, label_lower) → node
        b_index: dict[tuple[str, str], dict] = {}
        for nb in nodes_b:
            key = (nb.get("node_type", ""), nb.get("label", "").lower())
            b_index[key] = nb

        for na in nodes_a:
            key = (na.get("node_type", ""), na.get("label", "").lower())
            if key in b_index:
                nb = b_index[key]
                node_type = na.get("node_type", "")
                label = na.get("label", "")

                events.append(FusionEvent(
                    event_type="node_shared",
                    source=f"paper_a:{na.get('node_id')}",
                    target=f"paper_b:{nb.get('node_id')}",
                    description=f"匹配到共享 {node_type}: {label}",
                    properties={
                        "node_id_a": na.get("node_id"),
                        "node_id_b": nb.get("node_id"),
                        "node_type": node_type,
                        "label": label,
                    },
                ))
                result.merged_nodes.append({
                    "paper_a": na,
                    "paper_b": nb,
                })

        return events

    # ── 演化链检测 ──

    def _detect_evolution(
        self, pid_a: str, pid_b: str, result: FusionResult
    ) -> list[FusionEvent]:
        """检测方法演化链（extends 关系）。

        通过 LLM 判断两篇论文的 Method 节点之间是否存在技术扩展关系。
        在当前基础版本中，使用启发式方法（同名匹配）。
        完整 LLM 检测版本在后续迭代中实现。
        """
        events: list[FusionEvent] = []
        methods_a = self.engine.list_nodes_by_type("Method")
        methods_b = self.engine.list_nodes_by_type("Method")

        methods_a = [m for m in methods_a if m.get("paper_id") == pid_a]
        methods_b = [m for m in methods_b if m.get("paper_id") == pid_b]

        # 简单启发式：检查方法名中是否包含另一个方法名
        for ma in methods_a:
            label_a = (ma.get("label") or "").lower()
            for mb in methods_b:
                label_b = (mb.get("label") or "").lower()
                if label_a and label_b and (
                    label_a in label_b or label_b in label_a
                ):
                    events.append(FusionEvent(
                        event_type="evolution_detected",
                        source=f"pid_a:{ma.get('node_id')}",
                        target=f"pid_b:{mb.get('node_id')}",
                        description=f"检测到可能的演化关系: {ma.get('label')} → {mb.get('label')}",
                    ))
        return events

    # ── 矛盾检测 ──

    def _detect_contradictions(
        self, pid_a: str, pid_b: str, result: FusionResult
    ) -> list[FusionEvent]:
        """检测矛盾结论 (contradicts 关系)。

        需要 LLM 深度比较两篇论文的 Claim 节点。
        当前版本仅做骨架实现。
        """
        return []

    # ── 辅助 ──

    def get_fusion_summary(self, result: FusionResult) -> str:
        """生成融合结果的人类可读摘要。"""
        lines = []
        for event in result.events:
            if event.event_type == "node_shared":
                lines.append(f"🔗 {event.description}")
            elif event.event_type == "evolution_detected":
                lines.append(f"🔀 {event.description}")
            elif event.event_type == "contradiction_detected":
                lines.append(f"⚠️ {event.description}")
        return "\n".join(lines) if lines else "未检测到跨论文关联。"
