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

### planner 选节规范

第一阶段只看到 section manifest、页码、短摘要和线索字段时，要主动寻找能支撑核心卡片的章节，而不是等待标题完全匹配。

必须稳定规划：

- `field_overview`：至少 1 张，优先选 abstract、introduction、background、overview。
- `taxonomy`：有分类、类型、维度、form/function/dynamics/paradigm 等线索时规划 1-3 张。
- `technical_routes`：有 approach、framework、algorithm、mechanism、architecture、route、pipeline 等线索时规划 1-3 张。
- `representative_methods`：只要 manifest 中出现 citation、`et al.`、年份、具体系统/方法名、baseline、comparison、Table/Figure、benchmark、framework、algorithm，就必须规划。可以规划 3-8 个具体方法任务，或规划一个聚合型任务并允许第二阶段返回 `items[]`。
- `datasets`：有 dataset、benchmark、corpus、leaderboard、metric、evaluation table 等证据时必须规划，不要因为章节标题不是 Resources/Datasets 就省略。
- `evaluation_protocols`：有 metric、protocol、baseline、split、setting、human evaluation、automatic evaluation 等证据时必须规划。
- `open_challenges`：有 limitation、future、challenge、open problem、outlook、frontier、risk 等证据时必须规划。

每个任务必须说明 `evidence_reason`：为什么这些 section 可能支撑该卡片；同时给出 `expected_output_fields`，让第二阶段按字段生成。找不到核心 group 时才允许省略，并写 omission reason。

### Intro 全局上下文规范

第二阶段生成任何综述卡片时，都会额外收到完整或压缩后的 intro-like sections。Intro 只用于建立全局理解，不替代被选 section 的证据。

Intro 应重点用于：

- 抽取领域入口：研究对象、典型任务、输入输出、关键术语。
- 抽取发展路线：作者如何描述领域从早期方法走到当前问题。
- 抽取核心问题：论文开篇提出的 challenge、motivation、insight、research question。
- 抽取作者组织视角：综述按什么维度组织领域，为什么这样组织。
- 抽取新手前置知识：读后文前必须先懂的概念、路线、易混点和锚点论文。

不要把 intro 的泛化说法当作代表方法或数据集；代表方法和数据集必须以具体被选 section 中的证据为准。

### 综述前置知识卡片规范

综述论文的智能索引顶部必须优先生成 `prerequisite_card`，只从 intro-like sections 中抽取。结构应包含：

```json
{
  "concepts": [
    {
      "name": "概念名",
      "why_needed": "为什么读这篇综述前必须懂",
      "learn_first": ["更基础的小概念"],
      "difficulty": "easy|medium|hard",
      "evidence": "intro 中的依据",
      "source_sections": []
    }
  ],
  "field_questions": [
    {
      "question": "领域核心问题",
      "why_it_matters": "为什么它驱动这篇综述",
      "intro_evidence": "依据",
      "source_sections": []
    }
  ],
  "reading_order": [
    {
      "step": "第几步",
      "read": "先读什么",
      "why": "为什么这样读",
      "source_sections": []
    }
  ],
  "anchor_works": [
    {
      "title": "论文或系统名，不确定则留空",
      "year": "年份，不确定则留空",
      "relationship": "foundational_work|survey_anchor|baseline|benchmark|method_family",
      "why_read": "为什么适合作为锚点",
      "url": "输入中没有就留空",
      "evidence": "依据",
      "source_sections": []
    }
  ],
  "common_confusions": [
    {
      "pair": "容易混淆的两个概念",
      "difference": "区别",
      "why_confusing": "为什么新手会混",
      "evidence": "依据",
      "source_sections": []
    }
  ]
}
```

禁止泛泛罗列概念；禁止编造论文链接；没有明确 anchor work 时 `anchor_works` 可以为空。

### 卡片内容深度规范

每张正式卡片不能只是摘要，必须尽量包含：

- 是什么：对象、路线、方法、数据集或问题的明确名称。
- 解决什么：它面向的任务、痛点或局限。
- 具体机制/方案：关键步骤、模块、流程、数据结构或评测设置。
- 关系：和其它路线、章节、baseline、数据集或挑战的联系。
- 局限：适用边界、残留问题或公平比较注意事项。
- 证据：来自哪些 section、页码或原文线索。

如果证据不足，返回 `insufficient_evidence: true` 和原因，不要生成空泛卡。

### 代表方法强 schema

`representative_methods` 可以返回 `items[]`。每个 item 尽量包含：

```json
{
  "paper_title": "具体论文标题；原文没有就留空",
  "year": "年份；原文没有就留空",
  "method_name": "方法：使用了什么模型等等/系统/框架名",
  "route": "所属技术路线",
  "problem_addressed": "解决的问题",
  "core_mechanism": "核心机制",
  "specific_solution": "具体方案或模块",
  "improves_on": "相对什么 baseline/路线改进",
  "limitations": "局限",
  "evidence": "具体证据",
  "source_sections": []
}
```

没有论文标题时，可以展示“方法/系统/路线级代表方案”，但必须有 `method_name/core_mechanism/specific_solution/evidence`，不能把章节标题当方法。

### 数据集强 schema

`datasets` 可以返回 `items[]`。每个 item 尽量包含：

```json
{
  "name": "数据集或 benchmark 名称",
  "task": "任务",
  "content": "数据内容",
  "structure": "数据结构或标注形式",
  "scale": "规模；原文没有就留空",
  "metrics": "评测指标",
  "used_by_methods": ["哪些方法/路线使用"],
  "evidence": "具体证据",
  "source_sections": []
}
```

没有具体名称时不要作为正式公开数据集卡片。

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
