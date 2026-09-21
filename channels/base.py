"""定义外部 channel 消息与适配器基础接口。"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from bus.message_bus import MessageBus


_FENCED_CODE_RE = re.compile(
    r"^[ \t]*(?:```|~~~)(?:[A-Za-z0-9_+.-]+)?[ \t]*$",
    re.MULTILINE,
)
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
_MARKDOWN_TABLE_RULE_RE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$"
)
_RAW_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_JSON_FIELD_NAMES = {
    "field": "领域",
    "subfield": "子领域",
    "tech_lineage": "技术谱系",
    "predecessor": "前序工作",
    "relationship": "关系",
    "successor": "后续工作",
    "position": "领域定位",
    "uniqueness": "独特性",
    "related_works": "相关工作",
    "title": "标题",
    "arxiv_id": "arXiv ID",
    "field_trends": "领域趋势",
}


def _render_json_as_text(value: Any, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            label = _JSON_FIELD_NAMES.get(str(key), str(key))
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{label}：")
                lines.extend(_render_json_as_text(item, indent + 1))
            else:
                shown = "" if item is None else str(item)
                lines.append(f"{prefix}{label}：{shown}")
        return lines

    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                lines.append(f"{prefix}{index}.")
                lines.extend(_render_json_as_text(item, indent + 1))
            elif isinstance(item, list):
                lines.append(f"{prefix}{index}.")
                lines.extend(_render_json_as_text(item, indent + 1))
            else:
                lines.append(f"{prefix}• {item}")
        return lines

    return [f"{prefix}{value}"]


def _convert_multiline_json_blocks(text: str) -> str:
    """Replace valid multi-line JSON objects with readable field lists."""

    replacements: list[tuple[int, int, str]] = []
    start: int | None = None
    stack: list[str] = []
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"' and stack:
            in_string = True
            continue
        if char in "{[":
            if not stack:
                start = index
            stack.append(char)
            continue
        if char not in "}]" or not stack:
            continue

        opening = stack[-1]
        if (opening, char) not in {("{", "}"), ("[", "]")}:
            stack.clear()
            start = None
            continue
        stack.pop()
        if stack or start is None:
            continue

        candidate = text[start:index + 1]
        if "\n" not in candidate and len(candidate) < 120:
            start = None
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            start = None
            continue
        if isinstance(parsed, (dict, list)):
            replacements.append(
                (start, index + 1, "\n".join(_render_json_as_text(parsed)))
            )
        start = None

    for begin, end, replacement in reversed(replacements):
        text = f"{text[:begin]}{replacement}{text[end:]}"
    return text


def _latex_to_plain_text(expression: str) -> str:
    """Convert common generated LaTeX fragments to readable plain text."""

    value = str(expression or "").strip()
    value = re.sub(r"\\(?:left|right)\b", "", value)
    value = re.sub(
        r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
        r"(\1)/(\2)",
        value,
    )
    value = re.sub(r"\\sqrt\s*\{([^{}]+)\}", r"√(\1)", value)
    value = re.sub(
        r"\\(?:text|mathrm|mathbf|mathit)\s*\{([^{}]+)\}",
        r"\1",
        value,
    )

    replacements = {
        r"\Longleftrightarrow": "⇔",
        r"\Leftrightarrow": "⇔",
        r"\longrightarrow": "→",
        r"\rightarrow": "→",
        r"\leftarrow": "←",
        r"\geq": "≥",
        r"\leq": "≤",
        r"\neq": "≠",
        r"\approx": "≈",
        r"\times": "×",
        r"\cdot": "·",
        r"\infty": "∞",
        r"\alpha": "α",
        r"\beta": "β",
        r"\gamma": "γ",
        r"\delta": "δ",
        r"\theta": "θ",
        r"\lambda": "λ",
        r"\mu": "μ",
        r"\sigma": "σ",
        r"\phi": "φ",
        r"\omega": "ω",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)

    value = re.sub(
        r"_\{([^{}]+)\}",
        lambda match: (
            f"_{match.group(1)}"
            if len(match.group(1)) == 1
            else f"_({match.group(1)})"
        ),
        value,
    )
    value = re.sub(
        r"\^\{([^{}]+)\}",
        lambda match: (
            f"^{match.group(1)}"
            if len(match.group(1)) == 1
            else f"^({match.group(1)})"
        ),
        value,
    )
    value = value.replace("{", "").replace("}", "")
    # Keep unknown command names readable instead of leaking a backslash.
    value = re.sub(r"\\([A-Za-z]+)", r"\1", value)
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _strip_latex_delimiters(text: str) -> str:
    value = re.sub(
        r"\$\$(.+?)\$\$",
        lambda match: _latex_to_plain_text(match.group(1)),
        text,
        flags=re.DOTALL,
    )
    value = re.sub(
        r"\\\[(.+?)\\\]",
        lambda match: _latex_to_plain_text(match.group(1)),
        value,
        flags=re.DOTALL,
    )
    value = re.sub(
        r"\\\((.+?)\\\)",
        lambda match: _latex_to_plain_text(match.group(1)),
        value,
        flags=re.DOTALL,
    )
    return re.sub(
        r"(?<!\\)\$([^$\n]+?)(?<!\\)\$",
        lambda match: _latex_to_plain_text(match.group(1)),
        value,
    )


def format_plain_text(
    content: Any,
    *,
    preserve_urls: bool = True,
) -> str:
    """Turn Markdown-oriented model output into readable platform text.

    QQ C2C and the Feishu ``text`` message type do not render arbitrary
    Markdown.  Keeping the conversion here gives every plain-text channel the
    same behaviour without changing the content persisted for the web UI.
    """

    text = html.unescape(str(content or ""))
    text = _FENCED_CODE_RE.sub("", text)
    text = _strip_latex_delimiters(text)
    text = _convert_multiline_json_blocks(text)

    # Keep link destinations visible because citations and paper links are
    # useful in a chat client, while removing Markdown's label syntax.
    if preserve_urls:
        text = _MARKDOWN_LINK_RE.sub(
            lambda match: (
                f"{match.group(1)}（{match.group(2)}）"
                if match.group(1).strip() and match.group(1).strip() != match.group(2)
                else match.group(2)
            ),
            text,
        )
    else:
        text = _MARKDOWN_LINK_RE.sub(
            lambda match: match.group(1).strip(),
            text,
        )

    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _MARKDOWN_TABLE_RULE_RE.match(line):
            continue
        if re.match(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$", line):
            continue

        # Headings, quotes and list markers are presentation syntax.  Replace
        # lists with a Unicode bullet/number so their structure stays readable.
        line = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*", "", line)
        line = re.sub(r"^[ \t]{0,3}(?:>[ \t]?)+", "", line)
        line = re.sub(r"^[ \t]*[-+*][ \t]+", "• ", line)
        line = re.sub(r"^[ \t]*(\d+)[.)][ \t]+", r"\1. ", line)
        line = re.sub(r"^•[ \t]+\[[xX ]\][ \t]+", "• ", line)

        # Convert Markdown tables to ordinary text separators.
        if line.count("|") >= 2:
            line = line.strip().strip("|")
            line = " ｜ ".join(part.strip() for part in line.split("|"))

        # Unwrap inline emphasis/code.  These substitutions deliberately
        # require paired delimiters so underscores inside URLs remain intact.
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"~~([^~]+)~~", r"\1", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"__([^_]+)__", r"\1", line)
        line = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", line)
        line = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"\1", line)
        line = re.sub(r"<(https?://[^>]+)>", r"\1", line)
        line = re.sub(r"</?[A-Za-z][^>]*>", "", line)
        line = re.sub(r"\\([\\`*{}\[\]()#+.!_>~-])", r"\1", line)
        line = line.replace("**", "").replace("__", "")
        if not preserve_urls:
            had_raw_url = bool(_RAW_URL_RE.search(line))
            line = _RAW_URL_RE.sub("", line)
            line = re.sub(r"[ \t]+([，。；：！？,.!?:;])", r"\1", line)
            indentation = line[: len(line) - len(line.lstrip(" \t"))]
            body = re.sub(r"[ \t]{2,}", " ", line[len(indentation):])
            line = f"{indentation}{body}"
            line = re.sub(r"[（(][ \t]*[）)]", "", line)
            if had_raw_url and re.fullmatch(r"[ \t]*[^：:\n]{0,30}[：:][ \t]*", line):
                continue
            if line.strip() == "•":
                continue
        lines.append(line.rstrip())

    # Avoid the large vertical gaps commonly produced by generated Markdown.
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# 表示外部 channel 进入或离开系统时的统一消息。
@dataclass(slots=True)
class ChannelMessage:
    session_id: str
    channel: str
    direction: str
    mode: str
    content: Any
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# 定义所有外部 channel 需要实现的最小接口。
class BaseChannel:
    name: str
    bus: MessageBus

    # 启动 channel 监听。
    def start(self) -> None:
        raise NotImplementedError

    # 停止 channel 监听。
    def stop(self) -> None:
        raise NotImplementedError

    # 将外部原始输入转换为统一 ChannelMessage。
    async def receive_message(self, source: Any, mode: str) -> ChannelMessage:
        raise NotImplementedError

    # 将外部收到的 inbound message 发布到 bus。
    def publish_inbound(self, message: ChannelMessage) -> None:
        raise NotImplementedError

    # 将 outbound message 发送回外部平台。
    def send_outbound(self, message: ChannelMessage) -> None:
        raise NotImplementedError
