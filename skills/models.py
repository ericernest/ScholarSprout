"""定义 Skill 元数据、正文和能力选择结果。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SkillCategory = Literal["domain", "reading", "chat", "custom"]
SkillSource = Literal["builtin", "user"]


# 描述 SKILL.md Front Matter 中的字段。
class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    category: SkillCategory
    description: str
    when_to_use: list[str] = Field(default_factory=list)
    when_not_to_use: list[str] = Field(default_factory=list)

    # 校验必要字符串字段不为空。
    @field_validator("id", "name", "description")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        return normalized

    # 清理列表字段并拒绝空名称。
    @field_validator("when_to_use", "when_not_to_use")
    @classmethod
    def validate_string_list(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("must not contain empty values")
        return normalized


# 描述 Registry 对外提供的简短 Skill 摘要。
class SkillSummary(SkillMetadata):
    source: SkillSource


# 描述按需加载后的完整 Skill。
class SkillDocument(BaseModel):
    metadata: SkillSummary
    instructions: str

    # 校验 Skill 正文不为空。
    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Skill instructions must not be empty")
        return normalized


# 描述一次请求选择的零个或一个专项 Skill。
class CapabilitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str | None = None
    reason: str = ""

    # 清理专项 Skill 名称并拒绝空字符串。
    @field_validator("skill")
    @classmethod
    def validate_skill(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("skill must be null or a non-empty string")
        return normalized
