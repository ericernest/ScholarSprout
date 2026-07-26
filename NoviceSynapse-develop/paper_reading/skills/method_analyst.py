"""Method Analyst 输出后处理。"""

from __future__ import annotations

from typing import Any

from paper_reading.skills.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "auto") -> dict[str, Any]:
    data = extract_json_object(text)
    kg_nodes = []
    kg_edges = []
    if data:
        for index, step in enumerate(data.get("pipeline", []) or [], start=1):
            if not isinstance(step, dict):
                continue
            ref = step.get("step_id") or f"step_{index}"
            kg_nodes.append({
                "ref": f"module:{ref}",
                "node_type": "Module",
                "label": step.get("name") or ref,
                "paper_id": paper_id,
                "section_id": section_id,
                "properties": {
                    "description": step.get("description", ""),
                    "motivation": step.get("motivation", ""),
                    "is_contribution": bool(step.get("is_core_innovation", False)),
                    "read_stage": "method",
                },
            })
    return make_skill_output(
        "reading.method_analyst",
        "方法论分析师",
        "method_pipeline",
        text,
        data,
        trigger=trigger,
        kg_candidates={"nodes": kg_nodes, "edges": kg_edges},
    )
