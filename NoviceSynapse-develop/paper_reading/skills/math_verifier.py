"""Math Verifier 输出后处理。"""

from __future__ import annotations

from typing import Any

from paper_reading.skills.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "fork") -> dict[str, Any]:
    data = extract_json_object(text)
    kg_nodes = []
    if data and data.get("formula"):
        kg_nodes.append({
            "ref": "concept:formula",
            "node_type": "Concept",
            "label": str(data.get("formula", ""))[:120],
            "paper_id": paper_id,
            "section_id": section_id,
            "properties": {
                "definition": data.get("context", ""),
                "detected_gaps": data.get("detected_gaps", []),
                "read_stage": "method",
            },
        })
    return make_skill_output(
        "reading.math_verifier",
        "公式推导验证者",
        "math_derivation",
        text,
        data,
        trigger=trigger,
        kg_candidates={"nodes": kg_nodes, "edges": []},
    )
