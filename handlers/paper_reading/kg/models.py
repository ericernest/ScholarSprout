"""13种知识图谱节点类型 + 9种边类型 Pydantic 模型。

节点（13种）:
  Problem, Method, Module, Baseline, Metric, Dataset,
  Experiment, Figure, Concept, Limitation, Claim, RelatedWork, Insight

边（9种）:
  motivates, extends, outperforms, depends_on, contradicts,
  ablates, inspired_by, evaluated_on, contributes_to
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 节点基类 ──

class KGNode(BaseModel):
    """知识图谱节点基类。

    所有 13 种节点类型由此派生，通过 node_type 字段区分。
    """

    node_id: str = Field(default="", description="节点唯一 ID")
    node_type: str = Field(default="", description="节点类型枚举值")
    label: str = Field(default="", description="人类可读的简短标签")
    paper_id: str = Field(default="", description="所属论文 ID")
    section_id: str = Field(default="", description="来源章节 ID")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="类型特定的属性字典",
    )
    created_at: str = Field(default="", description="创建时间 ISO 时间戳")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="LLM 提取可信度 0-1",
    )


# ── 13 种节点类型 ──

class ProblemNode(KGNode):
    """研究问题节点。

    示例: "Few-shot Learning: Given K examples per class, learn to classify new examples"
    """

    node_type: Literal["Problem"] = "Problem"
    label: str = ""
    description: str = ""
    # properties 建议键: name, formulation, constraints, domain


class MethodNode(KGNode):
    """提出的方法节点。

    示例: "Prototypical Networks", category: "metric-learning"
    """

    node_type: Literal["Method"] = "Method"
    label: str = ""
    description: str = ""
    # properties 建议键: name, category, pipeline(JSON), is_proposed


class ModuleNode(KGNode):
    """方法子模块节点。

    示例: "Feature Extractor (CNN)", is_contribution: false
    """

    node_type: Literal["Module"] = "Module"
    label: str = ""
    description: str = ""
    # properties 建议键: name, parent_method, is_contribution, description


class BaselineNode(KGNode):
    """对比基线节点。

    示例: "MAML", category: "classic"
    """

    node_type: Literal["Baseline"] = "Baseline"
    label: str = ""
    description: str = ""
    # properties 建议键: name, paper_ref, category, description


class MetricNode(KGNode):
    """评估指标节点。

    示例: "Accuracy@1", domain: "classification"
    """

    node_type: Literal["Metric"] = "Metric"
    label: str = ""
    description: str = ""
    # properties 建议键: name, domain, higher_is_better


class DatasetNode(KGNode):
    """数据集节点。

    示例: "miniImageNet", size: {train:38400, val:9600, test:9600}
    """

    node_type: Literal["Dataset"] = "Dataset"
    label: str = ""
    description: str = ""
    # properties 建议键: name, size, domain, standard_split


class ExperimentNode(KGNode):
    """实验节点。

    示例: "5-way 1-shot", experiments 表中的每一行
    """

    node_type: Literal["Experiment"] = "Experiment"
    label: str = ""
    description: str = ""
    # properties 建议键: experiment_id, setting(JSON), results(JSON)


class FigureNode(KGNode):
    """图表节点。

    示例: "Architecture overview", type: "architecture"
    """

    node_type: Literal["Figure"] = "Figure"
    label: str = ""
    description: str = ""
    # properties 建议键: figure_id, caption, figure_type, key_insight


class ConceptNode(KGNode):
    """概念/术语节点。

    示例: "Attention", domain: "NLP"
    """

    node_type: Literal["Concept"] = "Concept"
    label: str = ""
    description: str = ""
    # properties 建议键: name, definition, domain, aliases


class LimitationNode(KGNode):
    """论文局限性节点。

    示例: "Only tested on English datasets", severity: 3
    """

    node_type: Literal["Limitation"] = "Limitation"
    label: str = ""
    description: str = ""
    # properties 建议键: description, severity(1-5), source_section


class ClaimNode(KGNode):
    """论文声明节点。

    示例: "Our method achieves SOTA", evidence_level: "experimental"
    """

    node_type: Literal["Claim"] = "Claim"
    label: str = ""
    description: str = ""
    # properties 建议键: statement, evidence_level(experimental/theoretical/anecdotal), verified


class RelatedWorkNode(KGNode):
    """相关工作节点。

    示例: "MAML (Finn et al. 2017)", relationship: "precursor"
    """

    node_type: Literal["RelatedWork"] = "RelatedWork"
    label: str = ""
    description: str = ""
    # properties 建议键: paper_title, arxiv_id, relationship(precursor/contemporary/successor/alternative)


class InsightNode(KGNode):
    """用户/Agent 洞察节点。用户自己记录的理解和想法。"""

    node_type: Literal["Insight"] = "Insight"
    label: str = ""
    description: str = ""
    # properties 建议键: content, author(user|agent), tags, related_nodes


# ── 边基类 ──

class KGEdge(BaseModel):
    """知识图谱边基类。"""

    edge_id: str = Field(default="", description="边唯一 ID")
    source_id: str = Field(default="", description="源节点 node_id")
    target_id: str = Field(default="", description="目标节点 node_id")
    edge_type: str = Field(default="", description="边类型枚举值")
    label: str = Field(default="", description="人类可读的关系描述")
    paper_id: str = Field(default="", description="所属论文 ID")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="类型特定的属性字典",
    )
    created_at: str = Field(default="", description="创建时间 ISO 时间戳")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="LLM 提取可信度 0-1",
    )


# ── 9 种边类型 ──

class MotivatesEdge(KGEdge):
    """A 驱动了 B 的提出: Problem → Method."""

    edge_type: Literal["motivates"] = "motivates"


class ExtendsEdge(KGEdge):
    """A 是 B 的技术扩展: Baseline/Method → Method.

    properties 建议键: key_difference
    """

    edge_type: Literal["extends"] = "extends"


class OutperformsEdge(KGEdge):
    """A 在实验中超越 B: Method → Baseline.

    properties 建议键: metric, margin, experiment_ref
    """

    edge_type: Literal["outperforms"] = "outperforms"


class DependsOnEdge(KGEdge):
    """A 依赖 B: Module → Concept / Module → Module."""

    edge_type: Literal["depends_on"] = "depends_on"


class ContradictsEdge(KGEdge):
    """A 与 B 存在矛盾: Claim → Claim.

    properties 建议键: description, resolution
    """

    edge_type: Literal["contradicts"] = "contradicts"


class AblatesEdge(KGEdge):
    """消融实验关系: Module → Experiment.

    properties 建议键: effect, experiment_ref
    """

    edge_type: Literal["ablates"] = "ablates"


class InspiredByEdge(KGEdge):
    """A 给了 B 灵感: RelatedWork → Module / Concept → Module."""

    edge_type: Literal["inspired_by"] = "inspired_by"


class EvaluatedOnEdge(KGEdge):
    """A 在 B 上评测: Experiment → Dataset."""

    edge_type: Literal["evaluated_on"] = "evaluated_on"


class ContributesToEdge(KGEdge):
    """模块对方法的贡献: Module → Method.

    properties 建议键: contribution_type (core_innovation|supporting|baseline)
    """

    edge_type: Literal["contributes_to"] = "contributes_to"


# ── 类型映射表 ──

NODE_TYPE_MAP: dict[str, type[KGNode]] = {
    "Problem": ProblemNode,
    "Method": MethodNode,
    "Module": ModuleNode,
    "Baseline": BaselineNode,
    "Metric": MetricNode,
    "Dataset": DatasetNode,
    "Experiment": ExperimentNode,
    "Figure": FigureNode,
    "Concept": ConceptNode,
    "Limitation": LimitationNode,
    "Claim": ClaimNode,
    "RelatedWork": RelatedWorkNode,
    "Insight": InsightNode,
}

EDGE_TYPE_MAP: dict[str, type[KGEdge]] = {
    "motivates": MotivatesEdge,
    "extends": ExtendsEdge,
    "outperforms": OutperformsEdge,
    "depends_on": DependsOnEdge,
    "contradicts": ContradictsEdge,
    "ablates": AblatesEdge,
    "inspired_by": InspiredByEdge,
    "evaluated_on": EvaluatedOnEdge,
    "contributes_to": ContributesToEdge,
}

ALL_NODE_TYPES = list(NODE_TYPE_MAP.keys())
ALL_EDGE_TYPES = list(EDGE_TYPE_MAP.keys())
