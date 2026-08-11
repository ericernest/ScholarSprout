"""论文精读 Skill 输出后处理公共工具。"""

from __future__ import annotations

import json
import re
from typing import Any


HIDDEN_STRUCTURED_KEYS = {
    "kg",
    "knowledge_graph",
    "dependency_graph",
    "graph",
    "nodes",
    "edges",
    "cytoscape",
    "cytoscape_elements",
}


def _escape_unquoted_inner_quotes(text: str) -> str:
    """Repair unescaped quotes inside JSON strings without closing truncation."""
    repaired: list[str] = []
    in_string = False
    escaped = False
    length = len(text)
    for index, character in enumerate(text):
        if not in_string:
            repaired.append(character)
            if character == '"':
                in_string = True
            continue
        if escaped:
            repaired.append(character)
            escaped = False
            continue
        if character == "\\":
            repaired.append(character)
            escaped = True
            continue
        if character != '"':
            repaired.append(character)
            continue

        next_index = index + 1
        while next_index < length and text[next_index].isspace():
            next_index += 1
        next_character = text[next_index] if next_index < length else ""
        if not next_character or next_character in ":,}]":
            repaired.append(character)
            in_string = False
        else:
            repaired.append('\\"')
    return "".join(repaired)


def _load_json_object(candidate: str) -> dict[str, Any] | None:
    for text in (candidate, _escape_unquoted_inner_quotes(candidate)):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从 LLM 文本中提取 JSON object。"""
    raw = (text or "").strip()
    if not raw:
        return None
    parsed = _load_json_object(raw)
    if parsed is not None:
        return parsed

    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if match:
        parsed = _load_json_object(match.group(1))
        if parsed is not None:
            return parsed

    # Accept only balanced top-level objects. A truncated outer object may
    # contain valid inner objects; returning one of them would silently turn a
    # broken reading map into a plausible but incomplete payload.
    depth = 0
    array_depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, character in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "[" and depth == 0:
            array_depth += 1
        elif character == "]" and depth == 0 and array_depth:
            array_depth -= 1
        elif character == "{" and array_depth == 0:
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and array_depth == 0 and depth:
            depth -= 1
            if depth == 0 and start is not None:
                parsed = _load_json_object(raw[start : index + 1])
                if parsed is not None:
                    return parsed
                start = None
    return None


def sanitize_structured_output(value: Any) -> Any:
    """Remove graph/KG implementation fields before returning Skill output."""
    if isinstance(value, list):
        return [
            item
            for item in (sanitize_structured_output(item) for item in value)
            if item not in ({}, [], None)
        ]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).lower()
        if normalized in HIDDEN_STRUCTURED_KEYS:
            continue
        if normalized.endswith("_graph") or normalized.endswith("_nodes") or normalized.endswith("_edges"):
            continue
        cleaned_item = sanitize_structured_output(item)
        if cleaned_item in ({}, [], None):
            continue
        cleaned[key] = cleaned_item
    return cleaned


def render_dict_markdown(title: str, data: dict[str, Any] | None, fallback: str) -> str:
    """把结构化结果渲染成简洁 Markdown。"""
    if not data:
        return fallback or ""
    lines = [f"### {title}"]
    for key, value in data.items():
        label = key.replace("_", " ")
        if isinstance(value, (str, int, float)):
            lines.append(f"- **{label}**: {value}")
        elif isinstance(value, list):
            lines.append(f"- **{label}**:")
            for item in value[:8]:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("description") or item.get("title") or json.dumps(item, ensure_ascii=False)
                    lines.append(f"  - {name}")
                else:
                    lines.append(f"  - {item}")
        elif isinstance(value, dict):
            nested = render_dict_markdown(label, value, "")
            if nested:
                lines.append(f"- **{label}**:")
                lines.extend(f"  {line}" for line in nested.splitlines()[1:])
    return "\n".join(lines)


def make_skill_output(
    skill_id: str,
    skill_name: str,
    output_type: str,
    text: str,
    data: dict[str, Any] | None,
    trigger: str = "auto",
) -> dict[str, Any]:
    """构造前端可消费的 SkillOutput-like dict。"""
    data = sanitize_structured_output(data) if data else None
    return {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "trigger": trigger,
        "output_type": output_type,
        "content": data or {"text": text or ""},
        "rendered": render_dict_markdown(skill_name, data, text),
        "parse_status": "parsed" if data else "raw_text",
    }
