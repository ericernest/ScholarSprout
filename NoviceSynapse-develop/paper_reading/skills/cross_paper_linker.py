"""Cross-Paper Linker 输出后处理。"""

from __future__ import annotations

from typing import Any

from paper_reading.skills.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "auto") -> dict[str, Any]:
    data = extract_json_object(text)
    return make_skill_output(
        "reading.cross_paper_linker",
        "跨论文连接器",
        "research_landscape",
        text,
        data,
        trigger=trigger,
    )
