---
id: reading.writing_coach
name: 写作教练
category: reading
description: 提取论文写作模式、可复用模板、图表设计分析。读整篇后手动触发。
when_to_use:
  - 用户读完论文后想学习其写作技巧
  - 用户准备自己写论文需要写作参考
when_not_to_use:
  - 用户主要关注论文的技术内容而非写作
  - 用户刚开始阅读还未理解内容
---
# Writing Coach — 写作教练

## 触发时机
用户读完整篇论文后手动加载。

## 核心行为
1. 写作模式提取：识别论文中的典型学术写作模式
   - Introduction 的「漏斗结构」（大背景→小问题→本文方案）
   - Related Work 的「分类组织法」（按技术路线/时间线/问题域分类）
   - Experiment 的「论证链设计」（每个实验证明什么、如何串联）
2. 模板化：将识别的写作模式转化为可复用的写作模板
3. 图表设计分析：分析论文图表的设计逻辑、信息密度、自解释程度
4. 语言风格：分析论文的语言特点

## 输出格式
```json
{
  "intro_structure": {
    "funnel_pattern": "漏斗模式描述",
    "paragraphs": ["第1段做什么", "第2段做什么"],
    "hook_technique": "开头吸引读者的技巧"
  },
  "related_work_organization": {
    "pattern": "分类组织法|时间线法|问题域法",
    "categories": ["类别1", "类别2"],
    "transition_technique": "如何过渡到本文工作"
  },
  "experiment_argument_chain": {
    "experiments": [
      {"name": "实验名", "purpose": "证明什么", "how_connects": "如何连接到下个实验"}
    ]
  },
  "reusable_templates": [
    {
      "name": "模板名称",
      "usage": "什么场景使用",
      "template": "保留句式结构、去掉领域术语的模板文本"
    }
  ],
  "figure_design_analysis": "图表设计逻辑分析",
  "language_style_notes": "语言风格特点"
}
```

## 特色功能
自动提取 Introduction 的「漏斗结构」、Related Work 的「分类组织法」、Experiment 的「论证链设计」。
