"""ScholarSprout 论文精读模块。

四层架构:
  Layer 1 — Paper Pipeline (论文获取流水线)
  Layer 2 — Knowledge Graph Engine (动态知识图谱引擎)
  Layer 3 — Postprocessors (现有 Skill 运行后的结构化后处理)
  Layer 4 — Harness (长效会话引擎)

真正的 Skill 定义位于 skills/builtin/reading，由统一 Skill Registry 加载。
注意: handler 模块依赖 FastAPI/channels 框架，采用惰性导入。
"""

__all__ = [
    "handler",
    "schemas",
    "pipeline",
    "kg",
    "postprocessors",
    "harness",
]
