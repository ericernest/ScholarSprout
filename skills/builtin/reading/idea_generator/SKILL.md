---
id: reading.idea_generator
name: 创新点生成器
category: reading
description: 基于论文Limitations生成3-5个可做的Follow-up Idea，每个锚定原文具体段落。读后触发。
when_to_use:
  - 用户读完论文想找下一步研究方向
  - 用户完成了全篇阅读进入反思阶段
when_not_to_use:
  - 用户尚未理解论文的核心局限
  - 用户只关心验证论文内容而非创新
---
# Idea Generator — 创新点生成器

## 触发时机
读完整篇论文后自动触发或手动加载。

## 核心行为
1. 局限锚定：从知识图谱中提取论文已记录的 Limitation 节点
2. 想法生成：针对每个核心局限，生成一个可行的后续研究方向
3. 可行性评级：对每个想法评定 High/Medium/Low 可行性
4. 综合说明：对生成的多个想法进行综合，指出它们之间的关联或互补关系

## 输出格式（最多5个想法）
```json
{
  "follow_up_ideas": [
    {
      "idea_title": "想法标题",
      "motivation_anchor": {
        "paper_section": "原文章节",
        "limitation_quote": "原文局限性的具体引用"
      },
      "approach": "具体方法思路",
      "difficulty": 3,
      "feasibility": "high|medium|low",
      "required_resources": "所需资源（数据/算力/知识）",
      "potential_pitfalls": ["潜在坑点1", "坑点2"],
      "expected_contribution": "预期贡献级别和类型"
    }
  ],
  "synthesis": "多个想法的综合说明，指出关联和互补"
}
```
## 防幻觉设计
每个 Idea 必须锚定在论文原文的具体 Limitation 段落，不可凭空生成。必须引用原文句子。
