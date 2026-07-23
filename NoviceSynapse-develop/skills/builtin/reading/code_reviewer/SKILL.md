---
id: reading.code_reviewer
name: 代码复现审查
category: reading
description: 检查开源代码与论文描述的一致性，评估复现难度（Easy/Medium/Hard）。用户手动加载。
when_to_use:
  - 用户阅读Implementation或实验设置相关章节时
  - 论文有开源代码或伪代码时
when_not_to_use:
  - 纯理论论文没有实现部分时
  - 用户不需要验证代码一致性时
---
# Code Reviewer — 代码复现审查

## 触发时机
用户主动加载，读 Implementation 章节时。需要工具支持：GitHub API 搜索。

## 核心行为
1. 检查是否有开源代码（使用 paper_search 搜索 GitHub）
2. 如果有代码：对比代码结构与论文描述的一致性
3. 如果没有代码：基于伪代码评估复现难度和潜在坑点
4. 提取关键超参数设置

## 输出格式
```json
{
  "code_availability": {
    "has_official_code": true,
    "repo_url": "https://github.com/...",
    "framework": "PyTorch|TensorFlow|JAX|...",
    "has_checkpoints": true,
    "license": "MIT"
  },
  "code_paper_consistency": [
    {
      "component": "Encoder / Decoder / Loss / ...",
      "paper_description": "论文中的描述",
      "code_implementation": "代码中的实现",
      "consistency": "consistent|minor_diff|major_diff|missing",
      "notes": "差异说明"
    }
  ],
  "reproducibility": {
    "difficulty": "Easy|Medium|Hard",
    "estimated_time": "预计复现时间",
    "key_challenges": ["挑战1", "挑战2"],
    "required_resources": "GPU需求 / 数据集获取难度",
    "pitfalls": [
      {
        "description": "潜在的坑",
        "severity": "high|medium|low",
        "mitigation": "如何避免"
      }
    ]
  },
  "hyperparameters": [
    {
      "name": "learning_rate",
      "value": "0.001",
      "paper_location": "Section 4.2",
      "sensitivity": "high|medium|low"
    }
  ]
}
```
