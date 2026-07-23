"""Domain Expert 输出后处理。"""

from __future__ import annotations

from typing import Any

from paper_reading.skills.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "manual") -> dict[str, Any]:
    data = extract_json_object(text)
    kg_nodes = []
    if data:
        for index, item in enumerate(data.get("related_work", []) or data.get("positioning", []) or [], start=1):
            if isinstance(item, dict):
                label = item.get("paper_title") or item.get("name") or item.get("title")
                properties = item
            else:
                label = str(item)
                properties = {"description": str(item)}
            if label:
                kg_nodes.append({
                    "ref": f"related_work:{index}",
                    "node_type": "RelatedWork",
                    "label": str(label)[:120],
                    "paper_id": paper_id,
                    "section_id": section_id,
                    "properties": {**properties, "read_stage": "introduction"},
                })
    return make_skill_output(
        "reading.domain_expert",
        "领域知识注入",
        "domain_context",
        text,
        data,
        trigger=trigger,
        kg_candidates={"nodes": kg_nodes, "edges": []},
    )
