"""解析 SKILL.md 的 Front Matter 和 Markdown 正文。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import SkillDocument, SkillMetadata, SkillSource, SkillSummary


class SkillLoadError(ValueError):
    """表示 SKILL.md 无法被可靠加载。"""


# 读取 YAML Front Matter，并按需读取 Markdown 正文。
def _read_skill_parts(path: Path, include_instructions: bool) -> tuple[str, str]:
    try:
        with path.open("r", encoding="utf-8-sig") as skill_file:
            if skill_file.readline().strip() != "---":
                raise SkillLoadError(f"Skill Front Matter is missing: {path}")

            metadata_lines: list[str] = []
            for line in skill_file:
                if line.strip() == "---":
                    instructions = skill_file.read() if include_instructions else ""
                    return "".join(metadata_lines), instructions
                metadata_lines.append(line)
    except OSError as error:
        raise SkillLoadError(f"Failed to read Skill file: {path}") from error

    raise SkillLoadError(f"Skill Front Matter is not closed: {path}")


# 使用 PyYAML 和 Pydantic 校验 Front Matter。
def _parse_metadata(metadata_text: str, path: Path) -> SkillMetadata:
    try:
        raw_metadata: Any = yaml.safe_load(metadata_text)
    except yaml.YAMLError as error:
        raise SkillLoadError(f"Failed to parse Skill Front Matter: {path}: {error}") from error

    if not isinstance(raw_metadata, dict):
        raise SkillLoadError(f"Skill Front Matter must be a YAML object: {path}")

    try:
        return SkillMetadata.model_validate(raw_metadata)
    except ValidationError as error:
        raise SkillLoadError(f"Invalid Skill metadata: {path}: {error}") from error


# 只读取 Skill 元数据，供 Registry 初始扫描使用。
def load_skill_metadata(path: Path) -> SkillMetadata:
    metadata_text, _ = _read_skill_parts(path, include_instructions=False)
    return _parse_metadata(metadata_text, path)


# 按需读取完整 Skill 正文。
def load_skill_document(path: Path, source: SkillSource) -> SkillDocument:
    metadata_text, instructions = _read_skill_parts(path, include_instructions=True)
    metadata = _parse_metadata(metadata_text, path)
    summary = SkillSummary(**metadata.model_dump(), source=source)

    try:
        return SkillDocument(metadata=summary, instructions=instructions)
    except ValidationError as error:
        raise SkillLoadError(f"Invalid Skill instructions: {path}: {error}") from error
