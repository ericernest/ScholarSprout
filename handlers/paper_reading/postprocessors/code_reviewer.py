"""Code Reviewer 输出后处理。"""

from __future__ import annotations

from typing import Any

from handlers.paper_reading.postprocessors.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "manual") -> dict[str, Any]:
    data = extract_json_object(text)
    return make_skill_output(
        "reading.code_reviewer",
        "代码复现审查",
        "code_reproduction_report",
        text,
        data,
        trigger=trigger,
    )
