"""Optional MinerU parsing adapter used alongside the local PDF parser."""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

import httpx

from config.schema import MinerUConfig


MINERU_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_ARCHIVE_MEMBERS = 2000
MAX_ARCHIVE_MEMBER_BYTES = 40 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 240 * 1024 * 1024
AUXILIARY_CONTENT_TYPES = {
    "header", "footer", "page_number", "aside_text", "page_footnote",
}


@dataclass(slots=True)
class MinerUImageAsset:
    source_path: str
    asset_name: str
    data: bytes


@dataclass(slots=True)
class MinerUParseResult:
    markdown: str
    raw_markdown: str = ""
    content_list: list[dict[str, Any]] = field(default_factory=list)
    middle_json: dict[str, Any] | list[Any] | None = None
    images: list[MinerUImageAsset] = field(default_factory=list)
    response_format: str = "json"


class MinerUClient:
    def __init__(self, config: MinerUConfig) -> None:
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(self.config.base_url and self.config.api_key.strip())

    def parse_pdf(self, pdf_bytes: bytes) -> MinerUParseResult:
        if not self.configured:
            raise RuntimeError("MinerU is not configured")
        with httpx.Client(trust_env=False, timeout=self.config.timeout) as client:
            response = client.post(
                str(self.config.base_url),
                headers={"Authorization": f"Bearer {self.config.api_key.strip()}"},
                files={"files": ("paper.pdf", pdf_bytes, "application/pdf")},
                data={
                    "return_md": "true",
                    "return_images": "true",
                    "return_content_list": "true",
                    "return_middle_json": "true",
                    "return_model_output": "false",
                    "return_original_file": "false",
                    "response_format_zip": "true",
                },
            )
            response.raise_for_status()
            result = parse_mineru_response(response)
        if len(result.markdown.strip()) < 80:
            raise RuntimeError("MinerU returned no usable Markdown or structured content")
        return result


def parse_mineru_response(response: httpx.Response) -> MinerUParseResult:
    """Parse the documented ZIP or JSON response without scanning arbitrary strings."""
    content_type = response.headers.get("content-type", "").lower()
    if "zip" in content_type or response.content.startswith(b"PK\x03\x04"):
        return _parse_zip_response(response.content)
    if "json" not in content_type:
        markdown = response.text.strip()
        return MinerUParseResult(markdown=markdown, raw_markdown=markdown, response_format="text")
    return _parse_json_response(response.json())


def _parse_zip_response(data: bytes) -> MinerUParseResult:
    markdown_candidates: list[tuple[str, str]] = []
    content_candidates: list[tuple[str, Any]] = []
    middle_candidates: list[tuple[str, Any]] = []
    images: list[MinerUImageAsset] = []
    total_size = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise RuntimeError("MinerU archive contains too many files")
        for member in members:
            normalized = _safe_archive_path(member.filename)
            if not normalized or member.is_dir():
                continue
            if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                raise RuntimeError(f"MinerU archive member is too large: {normalized}")
            total_size += member.file_size
            if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                raise RuntimeError("MinerU archive is too large")
            suffix = PurePosixPath(normalized).suffix.lower()
            basename = PurePosixPath(normalized).name.lower()
            raw = archive.read(member)
            if suffix == ".md":
                markdown_candidates.append((normalized, raw.decode("utf-8-sig", errors="replace")))
            elif suffix == ".json" and "content_list" in basename:
                content_candidates.append((normalized, _loads_json(raw)))
            elif suffix == ".json" and "middle" in basename:
                middle_candidates.append((normalized, _loads_json(raw)))
            elif suffix in MINERU_IMAGE_SUFFIXES and _looks_like_image_path(normalized):
                images.append(_image_asset(normalized, raw))

    raw_markdown = _best_text_candidate(markdown_candidates)
    content_list = _normalize_content_list(_best_json_candidate(content_candidates))
    middle_json = _best_json_candidate(middle_candidates)
    markdown = raw_markdown or _content_list_to_markdown(content_list)
    return MinerUParseResult(
        markdown=markdown.strip(),
        raw_markdown=raw_markdown.strip(),
        content_list=content_list,
        middle_json=middle_json,
        images=images,
        response_format="zip",
    )


def _parse_json_response(payload: Any) -> MinerUParseResult:
    container = _result_container(payload)
    raw_markdown = str(_named_value(container, ("md_content", "markdown", "md")) or "")
    content_list = _normalize_content_list(
        _decode_json_value(_named_value(container, ("content_list", "content_list_v2")))
    )
    middle_json = _decode_json_value(
        _named_value(container, ("middle_json", "middle_json_content", "middle"))
    )
    images = _json_images(_named_value(container, ("images", "image_files")))
    markdown = raw_markdown or _content_list_to_markdown(content_list)
    return MinerUParseResult(
        markdown=markdown.strip(),
        raw_markdown=raw_markdown.strip(),
        content_list=content_list,
        middle_json=middle_json if isinstance(middle_json, (dict, list)) else None,
        images=images,
        response_format="json",
    )


def _result_container(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    results = payload.get("results")
    if isinstance(results, dict) and results:
        return next((value for value in results.values() if isinstance(value, dict)), results)
    for key in ("result", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            resolved = _result_container(nested)
            if resolved is not nested or any(
                name in nested for name in ("md_content", "markdown", "content_list", "middle_json", "images")
            ):
                return resolved
    return payload


def _named_value(value: Any, names: tuple[str, ...]) -> Any:
    if not isinstance(value, dict):
        return None
    for name in names:
        if name in value:
            return value[name]
    for key in ("result", "results", "data"):
        nested = value.get(key)
        if isinstance(nested, dict):
            found = _named_value(nested, names)
            if found is not None:
                return found
    return None


def _json_images(value: Any) -> list[MinerUImageAsset]:
    if isinstance(value, list):
        normalized: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            source = item.get("path") or item.get("name") or item.get("img_path") or item.get("image_path")
            encoded = item.get("data") or item.get("base64") or item.get("content")
            if source and encoded:
                normalized[str(source)] = encoded
        value = normalized
    if not isinstance(value, dict):
        return []
    assets: list[MinerUImageAsset] = []
    for source_path, encoded in value.items():
        if isinstance(encoded, dict):
            encoded = encoded.get("data") or encoded.get("base64") or encoded.get("content")
        if not isinstance(encoded, str):
            continue
        if encoded.startswith("data:"):
            encoded = encoded.partition(",")[2]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            continue
        if not raw or len(raw) > MAX_ARCHIVE_MEMBER_BYTES:
            continue
        normalized = _safe_archive_path(str(source_path))
        if normalized and PurePosixPath(normalized).suffix.lower() in MINERU_IMAGE_SUFFIXES:
            assets.append(_image_asset(normalized, raw))
    return assets


def _image_asset(source_path: str, data: bytes) -> MinerUImageAsset:
    suffix = PurePosixPath(source_path).suffix.lower()
    digest = sha256(data).hexdigest()[:20]
    return MinerUImageAsset(
        source_path=source_path,
        asset_name=f"mineru-{digest}{suffix}",
        data=data,
    )


def _safe_archive_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    raw_path = PurePosixPath(raw)
    if not raw or raw_path.is_absolute() or ".." in raw_path.parts or re.match(r"^[A-Za-z]:", raw):
        return ""
    normalized = raw.lstrip("./")
    path = PurePosixPath(normalized)
    if not normalized or ".." in path.parts:
        return ""
    return str(path)


def _looks_like_image_path(value: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(value).parts]
    return "images" in parts or "image" in parts or len(parts) == 1


def _loads_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _normalize_content_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("content_list") or value.get("items") or []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _best_text_candidate(candidates: list[tuple[str, str]]) -> str:
    if not candidates:
        return ""
    return max(candidates, key=lambda item: len(item[1]))[1]


def _best_json_candidate(candidates: list[tuple[str, Any]]) -> Any:
    usable = [(name, value) for name, value in candidates if value is not None]
    if not usable:
        return None
    return max(usable, key=lambda item: len(item[1]) if isinstance(item[1], (list, dict)) else 0)[1]


def reflow_document(
    markdown: str,
    *,
    content_list: list[dict[str, Any]] | None = None,
    image_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convert MinerU output into the section contract used downstream."""
    structured_markdown = _content_list_to_markdown(content_list or [], image_urls=image_urls)
    source_markdown = structured_markdown if len(structured_markdown.strip()) >= 80 else markdown
    cleaned_markdown = _clean_reflow_markdown(source_markdown, image_urls=image_urls)
    lines = cleaned_markdown.splitlines()
    headings: list[tuple[int, str, list[str]]] = []
    preamble: list[str] = []
    current: tuple[int, str, list[str]] | None = None
    for raw in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw)
        if match:
            current = (len(match.group(1)), match.group(2).strip(), [])
            headings.append(current)
            continue
        (current[2] if current else preamble).append(raw)

    title = ""
    if headings and headings[0][0] == 1:
        title = headings[0][1]
        headings = headings[1:]
    if not title:
        title = next((line.strip() for line in preamble if len(line.strip()) > 5), "")

    sections: list[dict[str, Any]] = []
    for index, (level, section_title, body_lines) in enumerate(headings, start=1):
        # Preserve line structure inside Markdown blocks. Collapsing all
        # whitespace here destroys tables, display equations and fenced code.
        paragraphs = [
            block.strip()
            for block in re.split(r"\n\s*\n", "\n".join(body_lines))
            if block.strip()
        ]
        sections.append({
            "section_id": f"mineru:{index}",
            "title": section_title,
            "level": level,
            "content": "\n\n".join(paragraphs),
            "paragraphs": paragraphs,
            "start_page": None,
            "end_page": None,
        })
    abstract = ""
    for section in sections:
        if section["title"].strip().lower() in {"abstract", "摘要"}:
            abstract = section["content"][:3000]
            break
    return {
        "title": title[:300],
        "abstract": abstract,
        "sections": sections,
        "full_text": cleaned_markdown,
        "section_extraction_source": "mineru_markdown",
        "section_extraction_status": "mineru_used",
        "section_extraction_message": "已使用 MinerU 结构化内容和 AI 论文重排结果。",
        "outline_entries_count": len(sections),
    }


def _content_list_to_markdown(
    content_list: list[dict[str, Any]],
    *,
    image_urls: dict[str, str] | None = None,
) -> str:
    blocks: list[str] = []
    for item in content_list:
        kind = str(item.get("type") or "").strip().lower()
        if kind in AUXILIARY_CONTENT_TYPES:
            continue
        if kind in {"text", "title"}:
            text = str(item.get("text") or item.get("content") or "").strip()
            level = _safe_int(item.get("text_level"), default=1 if kind == "title" else 0)
            if text:
                blocks.append(f"{'#' * min(max(level, 1), 6)} {text}" if level else text)
        elif kind == "list":
            values = item.get("list_items") or item.get("items") or []
            if isinstance(values, list):
                rendered = [f"- {str(value).strip()}" for value in values if str(value).strip()]
                if rendered:
                    blocks.append("\n".join(rendered))
        elif kind in {"image", "chart"}:
            source = str(item.get("img_path") or item.get("image_path") or "").strip()
            url = _resolve_image_url(source, image_urls or {})
            captions = _text_list(item.get("image_caption") or item.get("img_caption") or item.get("chart_caption"))
            if url:
                blocks.append(f"![{captions[0] if captions else ''}]({url})")
            if captions:
                blocks.append("*" + " ".join(captions) + "*")
        elif kind == "table":
            captions = _text_list(item.get("table_caption"))
            body = _render_table_body(item.get("table_body") or item.get("body") or item.get("cells"))
            if captions:
                blocks.append("**" + " ".join(captions) + "**")
            if body:
                blocks.append(body)
        elif kind in {"equation", "interline_equation"}:
            equation = str(item.get("text") or item.get("latex") or item.get("content") or "").strip()
            if equation:
                if (
                    (equation.startswith("$$") and equation.endswith("$$"))
                    or (equation.startswith("\\[") and equation.endswith("\\]"))
                ):
                    blocks.append(equation)
                else:
                    blocks.append(f"$$\n{equation}\n$$")
        elif kind == "code":
            code = str(item.get("code_body") or item.get("text") or item.get("content") or "").strip()
            if code:
                blocks.append(f"```\n{code}\n```")
        else:
            text = str(item.get("text") or item.get("content") or "").strip()
            if text:
                blocks.append(text)
    return "\n\n".join(blocks)


def _render_table_body(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list) or not value:
        return ""
    rows = [row for row in value if isinstance(row, list)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [[_markdown_cell(row[index] if index < len(row) else "") for index in range(width)] for row in rows]
    return "\n".join([
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *("| " + " | ".join(row) + " |" for row in normalized[1:]),
    ])


def _markdown_cell(value: Any) -> str:
    return " ".join(str(value or "").replace("|", "\\|").split())


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def image_url_map(paper_id: str, images: list[MinerUImageAsset]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for image in images:
        url = f"/paper_reading/figures/{paper_id}/{image.asset_name}"
        source = _normalize_image_source(image.source_path)
        mapping[source] = url
        mapping[PurePosixPath(source).name] = url
        if "images/" in source.lower():
            offset = source.lower().rfind("images/")
            mapping[source[offset:]] = url
    return mapping


def _resolve_image_url(source: str, image_urls: dict[str, str]) -> str:
    normalized = _normalize_image_source(source)
    return image_urls.get(normalized) or image_urls.get(PurePosixPath(normalized).name) or ""


def _normalize_image_source(source: str) -> str:
    value = unquote(str(source or "").strip().strip("<>").strip('"\''))
    return value.replace("\\", "/").lstrip("./")


def _clean_reflow_markdown(markdown: str, *, image_urls: dict[str, str] | None = None) -> str:
    """Remove layout artifacts and retain only images that were actually persisted."""
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(
        r"<details>\s*<summary>line</summary>[\s\S]*?</details>",
        "",
        text,
        flags=re.IGNORECASE,
    )

    def replace_image(match: re.Match[str]) -> str:
        label, source = match.group(1), match.group(2)
        normalized_source = str(source or "").strip().strip("<>")
        if normalized_source.startswith("/paper_reading/figures/"):
            return f"![{label}]({normalized_source})"
        url = _resolve_image_url(source, image_urls or {})
        return f"![{label}]({url})" if url else ""

    # MinerU releases use images/, assets/, nested and URL-encoded paths.
    # Only persisted assets are rewritten; unknown paths are removed.
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_image, text, flags=re.IGNORECASE)
    text = re.sub(r"^Received\s+month\s+dd,\s*yyyy;.*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^E-?mail\s*:.*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(
        r"^\\?\*?Both authors contribute equally to this paper\.?$",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    text = _repair_conservative_dehyphenation(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _repair_conservative_dehyphenation(text: str) -> str:
    """Repair clear OCR line-break hyphens without merging compound terms."""
    pattern = re.compile(r"\b([A-Za-z]{2,4})-[ \t]*\n+[ \t]*([a-z]{2,})(?![-A-Za-z])")
    repaired = pattern.sub(lambda match: match.group(1) + match.group(2), text)
    return re.sub(r"([A-Za-z])-[ \t]*\n+[ \t]*(?=[a-z])", r"\1-", repaired)
