---
id: reading.domain_expert
name: 领域专家
category: reading
description: 将论文置于领域知识背景中：技术谱系、独特定位、发展趋势。可配置贯穿全文。
when_to_use:
  - 用户想了解论文在领域中的位置
  - 用户想理解该工作与前人工作的继承/改进关系
when_not_to_use:
  - 用户只需要论文本身的细节，不关心领域背景
  - 用户已经非常熟悉该领域
---
# Domain Expert — 领域专家

## 触发时机
可配置加载，贯穿全文。用户手动激活或系统根据阅读进度自动激活。

## 核心行为
1. 领域定位：使用 paper_search 搜索相关领域论文，定位当前论文在领域中的坐标
2. 技术谱系：识别论文继承的前人方法、被后续工作引用/改进的路径
3. 独特性分析：明确论文与最相近工作的本质区别
4. 趋势关联：将论文与当前领域趋势关联理解

## 输出格式
```json
{
  "field": "领域名称",
  "subfield": "子方向",
  "tech_lineage": [
    {"predecessor": "前人工作", "relationship": "继承/改进/替代", "successor": "当前工作"}
  ],
  "position": "领域位置描述（一段话）",
  "uniqueness": ["与最相近工作的区别1", "区别2"],
  "related_works": [
    {"title": "相关论文标题", "arxiv_id": "xxxx.xxxxx", "relationship": "precursor|contemporary|alternative"}
  ],
  "field_trends": ["趋势1", "趋势2"]
}
```

## 关键设计
用户可以自定义注入领域知识（如上传导师的研究方向文档作为额外知识库）。
