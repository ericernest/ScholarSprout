"""统一 Skill 后处理入口。"""

from __future__ import annotations

from typing import Any

from paper_reading.skills import (
    code_reviewer,
    critique_agent,
    cross_paper_linker,
    domain_expert,
    idea_generator,
    math_verifier,
    method_analyst,
    writing_coach,
)


POSTPROCESSORS = {
    "reading.method_analyst": method_analyst.postprocess,
    "reading.critique_agent": critique_agent.postprocess,
    "reading.math_verifier": math_verifier.postprocess,
    "reading.code_reviewer": code_reviewer.postprocess,
    "reading.domain_expert": domain_expert.postprocess,
    "reading.writing_coach": writing_coach.postprocess,
    "reading.idea_generator": idea_generator.postprocess,
    "reading.cross_paper_linker": cross_paper_linker.postprocess,
}


def postprocess_agent_output(
    text: str,
    skill_ids: list[str],
    paper_id: str = "",
    section_id: str = "",
    trigger: str = "auto",
) -> list[dict[str, Any]]:
    """对当前可能激活的 skills 执行后处理。"""
    outputs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for skill_id in skill_ids:
        if skill_id in seen:
            continue
        seen.add(skill_id)
        processor = POSTPROCESSORS.get(skill_id)
        if processor is None:
            continue
        outputs.append(processor(text, paper_id=paper_id, section_id=section_id, trigger=trigger))
    return outputs
