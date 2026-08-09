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


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从 LLM 文本中提取 JSON object。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if match:
        try:
            value = json.loads(match.group(1))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
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
