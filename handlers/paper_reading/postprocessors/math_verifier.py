"""Math Verifier output postprocessing."""

from __future__ import annotations

from typing import Any

from handlers.paper_reading.postprocessors.common import extract_json_object, make_skill_output


def postprocess(text: str, paper_id: str = "", section_id: str = "", trigger: str = "fork") -> dict[str, Any]:
    data = extract_json_object(text)
    return make_skill_output(
        "reading.math_verifier",
        "公式推导验证者",
        "math_derivation",
        text,
        data,
        trigger=trigger,
    )
