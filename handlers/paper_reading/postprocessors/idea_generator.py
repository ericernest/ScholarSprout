"""Idea Generator output postprocessing."""

from __future__ import annotations

from typing import Any

from handlers.paper_reading.postprocessors.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "auto") -> dict[str, Any]:
    data = extract_json_object(text)
    return make_skill_output(
        "reading.idea_generator",
        "创新点生成器",
        "idea_cards",
        text,
        data,
        trigger=trigger,
    )
