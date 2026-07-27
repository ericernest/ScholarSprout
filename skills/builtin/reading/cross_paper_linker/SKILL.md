---
id: reading.cross_paper_linker
name: 跨论文连接器
category: reading
description: 检测多篇论文间的概念共用、方法继承、实验对比、结论矛盾。跨论文自动触发。
when_to_use:
  - 用户的session中已添加多篇论文到知识图谱
  - 用户询问论文A和论文B的关系
when_not_to_use:
  - 只有一篇论文在知识图谱中
  - 用户没有发起跨论文分析请求
---
# Cross-Paper Linker — 跨论文连接器

## 触发时机
跨论文自动触发（当 KG 中检测到多篇论文时）。

## 核心行为
1. 共享概念检测：在知识图谱中搜索不同论文中相同或相似的概念节点
2. 方法继承链：通过 extends/inspired_by 边构建方法演进链
3. 实验对比：识别不同论文在相同数据集/指标上的实验节点，横向对比
4. 矛盾发现：在不同论文的 Claim 节点间寻找 contradicts 关系

## 输出格式
```json
{
  "shared_concepts": [
    {"concept": "概念名", "papers": ["paper_id_1", "paper_id_2"], "alignment": "consistent|different_context"}
  ],
  "method_evolution": [
    {"chain": ["方法A", "→", "方法B"], "description": "技术改进路径", "key_differences": ["差异1"]}
  ],
  "experimental_comparisons": [
    {"dataset": "数据集名", "metric": "指标名", "results": [{"paper": "A", "value": "..."}, {"paper": "B", "value": "..."}]}
  ],
  "contradictions": [
    {
      "topic": "矛盾主题",
      "paper_a_claim": "论文A的声明",
      "paper_b_claim": "论文B的声明",
      "analysis": "矛盾分析和可能的解释",
      "highlight": true
    }
  ],
  "landscape_summary": "领域全景总结（2-3句话）"
}
```

## 核心创新
自动检测跨论文的矛盾结论，高亮提示用户「这两篇论文对同一个问题得出了相反的结论，值得深挖」。
