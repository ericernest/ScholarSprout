"""Idea Generator 输出后处理。"""

from __future__ import annotations

from typing import Any

from handlers.paper_reading.postprocessors.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "auto") -> dict[str, Any]:
    data = extract_json_object(text)
    kg_nodes = []
    ideas = []
    if data:
        ideas = data.get("ideas", []) or data.get("idea_cards", []) or []
    for index, idea in enumerate(ideas[:8], start=1):
        if not isinstance(idea, dict):
            continue
        kg_nodes.append({
            "ref": f"insight:idea:{index}",
            "node_type": "Insight",
            "label": idea.get("title") or f"Follow-up Idea {index}",
            "paper_id": paper_id,
            "section_id": section_id,
            "properties": {**idea, "author": "agent", "read_stage": "conclusion"},
        })
    return make_skill_output(
        "reading.idea_generator",
        "创新点生成器",
        "idea_cards",
        text,
        data,
        trigger=trigger,
        kg_candidates={"nodes": kg_nodes, "edges": []},
    )
