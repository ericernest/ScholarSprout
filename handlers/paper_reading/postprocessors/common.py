"""论文精读 Skill 输出后处理公共工具。"""

from __future__ import annotations

import json
import re
from typing import Any


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
            lines.append(f"- **{label}**: {json.dumps(value, ensure_ascii=False)}")
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
    return {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "trigger": trigger,
        "output_type": output_type,
        "content": data or {"text": text or ""},
        "rendered": render_dict_markdown(skill_name, data, text),
        "parse_status": "parsed" if data else "raw_text",
    }
