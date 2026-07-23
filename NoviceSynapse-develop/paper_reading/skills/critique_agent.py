"""Critique Agent 输出后处理。"""

from __future__ import annotations

from typing import Any

from paper_reading.skills.common import extract_json_object, limitation_candidates, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "manual") -> dict[str, Any]:
    data = extract_json_object(text)
    candidates = limitation_candidates(data.get("weaknesses", []) if data else [], paper_id, section_id)
    return make_skill_output(
        "reading.critique_agent",
        "批判性审稿人",
        "critique_report",
        text,
        data,
        trigger=trigger,
        kg_candidates=candidates,
    )
