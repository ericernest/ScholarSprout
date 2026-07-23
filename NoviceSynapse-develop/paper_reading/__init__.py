"""NoviceSynapse 论文精读模块。

四层架构:
  Layer 1 — Paper Pipeline (论文获取流水线)
  Layer 2 — Knowledge Graph Engine (动态知识图谱引擎)
  Layer 3 — Skill 体系 (8 个内置 Skill)
  Layer 4 — Harness (长效会话引擎)

注意: handler 模块依赖 FastAPI/channels 框架，采用惰性导入。
"""

__all__ = [
    "handler",
    "schemas",
    "pipeline",
    "kg",
    "skills",
    "harness",
]
