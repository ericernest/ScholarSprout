"""扫描、索引并按需加载内置和用户 Skill。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .loader import SkillLoadError, load_skill_document, load_skill_metadata
from .models import SkillSource, SkillSummary

BUILTIN_CATEGORIES = ("domain", "reading", "chat", "custom")


class SkillRegistryError(ValueError):
    """表示 Skill 注册信息存在冲突或目录错误。"""


@dataclass(slots=True)
class SkillRecord:
    summary: SkillSummary
    path: Path


# 管理 Skill 元数据索引并延迟读取正文。
class SkillRegistry:
    # 扫描项目内置 Skill 和本地用户 Skill。
    def __init__(
        self,
        builtin_root: Path | None = None,
        user_root: Path | None = None,
    ) -> None:
        self.builtin_root = builtin_root or Path(__file__).resolve().parent / "builtin"
        self.user_root = user_root or Path.home() / ".novicesynapse" / "skills"
        self._records: dict[str, SkillRecord] = {}
        self._instructions: dict[str, str] = {}
        self.scan()

    # 重新建立稳定顺序的 Skill 元数据索引。
    def scan(self) -> None:
        self._records.clear()
        self._instructions.clear()
        self._scan_builtin_skills()
        self._scan_user_skills()

    # 扫描四类内置 Skill。
    def _scan_builtin_skills(self) -> None:
        if not self.builtin_root.exists():
            return

        allowed_paths: set[Path] = set()
        for category in BUILTIN_CATEGORIES:
            category_root = self.builtin_root / category
            if not category_root.exists():
                continue

            skill_paths = sorted(
                category_root.rglob("SKILL.md"),
                key=lambda path: path.as_posix(),
            )
            allowed_paths.update(skill_paths)
            for skill_path in skill_paths:
                self._register(skill_path, source="builtin", expected_category=category)

        unexpected_paths = sorted(
            set(self.builtin_root.rglob("SKILL.md")) - allowed_paths,
            key=lambda path: path.as_posix(),
        )
        if unexpected_paths:
            raise SkillRegistryError(
                f"Builtin Skill is outside allowed categories: {unexpected_paths[0]}"
            )

    # 递归扫描用户 Skill 目录。
    def _scan_user_skills(self) -> None:
        if not self.user_root.exists():
            return

        for skill_path in sorted(
            self.user_root.rglob("SKILL.md"),
            key=lambda path: path.as_posix(),
        ):
            self._register(skill_path, source="user")

    # 校验并注册单个 Skill 元数据。
    def _register(
        self,
        path: Path,
        source: SkillSource,
        expected_category: str | None = None,
    ) -> None:
        metadata = load_skill_metadata(path)
        if expected_category is not None and metadata.category != expected_category:
            raise SkillRegistryError(
                f"Skill category {metadata.category!r} does not match directory "
                f"{expected_category!r}: {path}"
            )

        if metadata.id in self._records:
            existing_path = self._records[metadata.id].path
            raise SkillRegistryError(
                f"Duplicate Skill id {metadata.id!r}: {existing_path} and {path}"
            )

        summary = SkillSummary(**metadata.model_dump(), source=source)
        self._records[metadata.id] = SkillRecord(summary=summary, path=path)

    # 根据 ID 获取 Skill 元数据摘要。
    def get_metadata(self, skill_id: str) -> SkillSummary:
        try:
            return self._records[skill_id].summary
        except KeyError as error:
            raise KeyError(f"Skill not registered: {skill_id}") from error

    # 在首次使用时读取并缓存 Skill 正文。
    def get_instructions(self, skill_id: str) -> str:
        if skill_id in self._instructions:
            return self._instructions[skill_id]

        try:
            record = self._records[skill_id]
        except KeyError as error:
            raise KeyError(f"Skill not registered: {skill_id}") from error

        document = load_skill_document(record.path, record.summary.source)
        if document.metadata.id != skill_id:
            raise SkillLoadError(
                f"Skill id changed after registry scan: {record.path}"
            )

        self._instructions[skill_id] = document.instructions
        return document.instructions

    # 将精确 ID 和 category.* 展开为稳定、有序且去重的 ID。
    def resolve_skill_ids(self, patterns: list[str]) -> list[str]:
        resolved: list[str] = []
        seen: set[str] = set()

        for pattern in patterns:
            if pattern.endswith(".*"):
                category = pattern[:-2]
                if category not in BUILTIN_CATEGORIES:
                    raise SkillRegistryError(f"Unsupported Skill category pattern: {pattern}")
                matches = [
                    skill_id
                    for skill_id, record in self._records.items()
                    if record.summary.category == category
                ]
            else:
                if pattern not in self._records:
                    raise KeyError(f"Skill not registered: {pattern}")
                matches = [pattern]

            for skill_id in matches:
                if skill_id not in seen:
                    resolved.append(skill_id)
                    seen.add(skill_id)

        return resolved

    # 列出全部 Skill 或指定候选范围的简短摘要。
    def list_summaries(self, patterns: list[str] | None = None) -> list[SkillSummary]:
        skill_ids = (
            self.resolve_skill_ids(patterns)
            if patterns is not None
            else list(self._records)
        )
        return [self._records[skill_id].summary for skill_id in skill_ids]


# 创建默认 SkillRegistry。
def create_skill_registry() -> SkillRegistry:
    return SkillRegistry()
