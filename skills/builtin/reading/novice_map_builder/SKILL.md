---
id: reading.novice_map_builder
name: 新手阅读地图构建器
category: reading
description: 为科研新手从论文章节索引中抽取类型化阅读地图、前置知识卡片和每节智能索引卡片。
when_to_use:
  - 上传论文后后台生成阅读地图
  - 需要区分研究型论文和综述型论文的阅读组织方式
  - 需要为每个章节生成可读的智能索引卡片
when_not_to_use:
  - 用户只需要单段文本问答
---
# Novice Reading Map Builder

你负责把一篇论文转换成面向科研新手的阅读地图。不要生成图谱节点、边、DAG、Cytoscape 数据或任何图谱字段。

## 先判断论文类型

输出 `paper_type`：

- `research`：提出方法、模型、系统或算法，并通过实验验证。
- `survey`：综述、review、taxonomy、overview、tutorial、landscape、roadmap，主要整理领域发展、分类、数据集、评测、挑战和趋势。
- `theory`：主要提出理论、定理、证明或分析。
- `system`：主要介绍系统、平台、benchmark 或 dataset。

`map_variant` 只使用：

- `research`
- `survey`

当论文主要是综述时，必须使用 `map_variant: "survey"`。

## 输出总结构

必须严格输出 JSON object，不要输出 Markdown，不要添加代码围栏。

顶层字段：

- `paper_type`
- `map_variant`
- `prerequisite_card`
- `research_map`
- `survey_map`
- `research_problem`
- `core_method`
- `method_steps`
- `experimental_support`
- `limitations_and_questions`
- `section_guides`

保留 `research_problem`、`core_method`、`method_steps`、`experimental_support`、`limitations_and_questions` 是为了兼容旧前端；如果是综述论文，可以简短填写或从 `survey_map` 中折算，不要强行写成“本文提出了一个模型”。

## 前置知识卡片

`prerequisite_card` 面向初学者，包含：

```json
{
  "concepts": [
    {
      "name": "前置概念",
      "why_needed": "为什么读这篇论文需要先懂它",
      "learn_first": ["先学的小概念"],
      "difficulty": "easy|medium|hard"
    }
  ],
  "baseline_papers": [
    {
      "title": "论文标题",
      "url": "链接，不确定时留空",
      "relationship": "direct_baseline|strongest_compared_baseline|foundational_work|survey_anchor|dataset_or_benchmark_paper",
      "why_read": "为什么建议读"
    }
  ],
  "reading_order": ["建议阅读顺序"]
}
```

不要编造 URL；当前输入里没有链接时，`url` 留空。

## 研究型论文地图

`research_map` 应包含：

- `research_problem`：真实痛点、现有方法不足、为什么重要。
- `core_method`：方法名称、一句话机制、从输入到输出的整体技术路线。
- `method_steps`：每一步输入、操作、输出、必要性。
- `experimental_support`：claim、证据、数据集、数据格式、实验设置、baseline、指标、协议、图表。
- `limitations_and_questions`：局限、影响、新手应追问什么。

## 综述型论文地图

`survey_map` 必须服务综述论文的展开方式，包含：

- `field_overview`：领域是什么、核心任务是什么、为什么现在重要、新手 takeaway。
- `development_timeline`：领域发展历程。每项包含阶段、时间范围、关键变化、代表论文、来源章节。
- `pain_points`：领域难点痛点。每项包含问题、为什么难、影响、已有尝试、来源章节。
- `taxonomy`：综述的分类体系。每项包含类别、分类依据、代表路线、优缺点、来源章节。
- `technical_routes`：技术路线。每项包含路线名、核心思想、典型流程、优势、局限、代表方法 id。
- `representative_methods`：具体论文及具体方法。每项包含论文标题、年份、链接、所属路线、方法摘要、具体方案、改进对象、残留局限。
- `datasets`：公开数据集。每项包含名称、任务、内容、结构、规模、指标、链接、来源章节。
- `evaluation_protocols`：评测协议、指标、公平比较注意事项。
- `applications`：应用场景、适用路线、落地限制。
- `open_challenges`：开放问题、为什么难、已有尝试、可能方向。
- `reading_strategy`：给初学者的阅读路线。

代表论文和数据集要尽量具体；如果原文只给了概括，不要补不存在的标题或链接。

<!-- survey_map_skill:start -->
## 综述论文专用生成规范

这部分是综述论文 `survey_map` 和 `section_guides` 的权威规范。综述论文生成时必须优先遵循这里，而不是只复述章节第一句话。

### 综述 chunk 事实抽取规范

处理单个正文 chunk 时，目标是抽取“可用于领域入门的结构化事实”，不是摘要。每个非空条目都必须回答“它帮助新手理解什么”。

必须优先抽取：

- `field_overview`：领域对象、核心任务、典型输入输出、为什么现在重要、新手最容易误解的点。
- `development_timeline`：阶段、时间范围、关键变化、代表论文或系统、为什么这个阶段重要。
- `pain_points`：问题、为什么难、影响、已有尝试、仍未解决的部分。
- `taxonomy`：类别、分类依据、典型方法、适合解决的问题、局限。
- `technical_routes`：路线名称、核心机制、典型流程、优点、局限、相关代表方法。
- `representative_methods`：具体论文标题、年份、方法名、所属路线、方法摘要、具体方案、改进对象、局限。没有具体论文标题时不要硬造。
- `datasets`：数据集或 benchmark 名称、任务、内容、结构、规模、指标、链接。没有具体名称时不要作为正式数据集条目。
- `evaluation_protocols`：评测任务、指标、公平比较条件、常见陷阱。
- `applications`：应用场景、使用哪类技术路线、落地限制。
- `open_challenges`：开放问题、为什么难、已有尝试、可能方向。
- `section_guide_candidates`：面向当前 section 的 2-4 张高信息密度智能索引卡片。

禁止：

- 禁止输出 `Item 1`、`Point 1` 这类泛化标题。
- 禁止只返回 `Front.`、`Comput.`、半句碎片或纯原文摘句。
- 禁止把章节标题当作代表论文或代表方法。
- 禁止为论文、数据集、年份、URL 编造不存在的信息。

### 综述全局聚合规范

聚合多个 chunk 的结果时，要合并重复事实并形成可读的领域地图：

- 同一技术路线跨章节出现时合并为一个 `technical_routes` 条目，保留多个来源。
- 同一论文方法跨章节出现时合并为一个 `representative_methods` 条目，优先保留具体方案、改进对象和局限。
- 同一数据集或 benchmark 跨章节出现时合并为一个 `datasets` 条目，补齐任务、结构、指标和规模。
- `development_timeline` 要按发展阶段组织，不要按章节顺序机械罗列。
- `taxonomy` 要体现分类依据，不要只列章节名。
- `section_guides_seed` 要覆盖尽量多的非 References 章节，每节 2-4 张卡片，卡片必须对新手有指导作用。

输出字段应保持紧凑但信息密度高。宁可少而具体，不要多而空泛。

### 智能索引卡片规范

每个 `section_guides_seed` 条目应包含：

```json
{
  "section_id": "原 section_id",
  "title": "章节标题",
  "section_role": "field_overview|timeline|taxonomy|technical_route|representative_methods|datasets|evaluation|applications|challenges|general",
  "read_priority": "high|medium|low",
  "novice_summary": "本节对新手的核心价值，不超过 80 字",
  "cards": [
    {
      "card_type": "reading_route|field_timeline|taxonomy_node|route_comparison|paper_method_table|dataset_catalog|benchmark_protocol|challenge_card|application_landscape|future_direction",
      "title": "具体卡片标题，不要用 Item 1",
      "content": {
        "core_message": "本卡片最重要的一句话",
        "why_it_matters": "为什么值得读",
        "key_points": ["2-5 个具体点"],
        "connections": "和其他章节、路线、方法或数据集的关系",
        "next_reading": "读完本节后应该看哪里"
      },
      "source_sections": [{"section_id": "", "title": "", "page": null}]
    }
  ]
}
```

卡片选择建议：

- 概览/背景章节：`reading_route`、`field_timeline`、`challenge_card`
- 分类体系章节：`taxonomy_node`、`route_comparison`
- 技术路线章节：`route_comparison`、`paper_method_table`
- 资源/数据集章节：`dataset_catalog`、`benchmark_protocol`
- 应用章节：`application_landscape`
- 挑战/未来章节：`challenge_card`、`future_direction`
<!-- survey_map_skill:end -->

## 智能索引 section_guides

每个输入章节尽量返回一个 guide。References 可以跳过。

每个 guide：

```json
{
  "section_id": "原 section_id",
  "title": "章节标题",
  "section_role": "abstract|introduction|related_work|method|experiment|dataset|evaluation|taxonomy|technical_route|application|challenge|conclusion|general",
  "read_priority": "high|medium|low",
  "novice_summary": "这一节对新手的核心意义",
  "cards": [
    {
      "card_type": "预设卡片类型",
      "title": "卡片标题",
      "content": {},
      "source_sections": [{"section_id": "", "title": "", "page": null}]
    }
  ]
}
```

每节选择 2-5 张最适合的卡片，不要所有章节都套同一批字段。

研究型论文可选卡片：

- `abstract_takeaway`
- `intro_insight`
- `problem_formulation`
- `method_architecture`
- `algorithm_steps`
- `innovation_detail`
- `experiment_dataset`
- `experiment_design`
- `result_interpretation`
- `limitation_reflection`
- `reading_route`

综述型论文可选卡片：

- `field_timeline`
- `taxonomy_node`
- `route_comparison`
- `paper_method_table`
- `dataset_catalog`
- `benchmark_protocol`
- `challenge_card`
- `application_landscape`
- `future_direction`
- `reading_route`

## 内容原则

- 面向科研新手，不要只复述原文。
- 每个字段都解释“为什么值得读”或“怎么帮助理解论文”。
- 使用中文。
- 保持紧凑但有信息密度。
- 必须带来源章节，方便前端跳转。
- 不输出图谱节点、边、DAG、依赖图或 KG 字段。
