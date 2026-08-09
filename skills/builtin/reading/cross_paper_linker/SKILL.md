---
id: reading.cross_paper_linker
name: 跨论文连接器
category: reading
description: 检测多篇论文间的概念共用、方法继承、实验对比、结论矛盾。跨论文自动触发。
when_to_use:
  - 用户询问当前论文与相关论文、baseline 或引用工作的关系
  - 用户询问论文A和论文B的关系
when_not_to_use:
  - 用户没有发起跨论文分析请求
---
# Cross-Paper Linker — 跨论文连接器

## 触发时机
当用户询问相关工作、引用脉络、baseline 对比或跨论文关系时触发。

## 核心行为
1. 共享概念检测：根据当前论文正文、引用、用户提供的论文和 paper_search 结果识别共用概念
2. 方法继承链：说明方法之间的继承、改进、替代或互补关系
3. 实验对比：识别相同数据集/指标上的横向比较
4. 矛盾发现：比较不同论文对同一问题的结论差异

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

## 输出原则
不要输出图谱节点、边或工具内部字段；如果链接或元数据不确定，留空或说明“不确定”，不要编造。
