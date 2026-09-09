"""Reliable remote PDF downloads shared by every paper-import surface."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx


MAX_PDF_BYTES = 128 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3
_RETRY_DELAYS = (0.5, 1.5)
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_ARXIV_URL_PATTERN = re.compile(
    r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/"
    r"(?P<arxiv_id>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)"
    r"(?:\.pdf)?(?:[?#].*)?$",
    re.IGNORECASE,
)
_HEADERS = {
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.5",
    "Accept-Encoding": "identity",
    "Connection": "close",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36 ScholarSprout/1.0"
    ),
}


class PDFDownloadError(RuntimeError):
    """Raised after all safe download attempts have failed."""


def _candidate_urls(source_url: str) -> list[str]:
    """Return the original PDF URL plus safe source-specific fallbacks."""

    source_url = source_url.strip()
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PDFDownloadError("PDF 地址必须是 http 或 https 链接")

    match = _ARXIV_URL_PATTERN.match(source_url)
    if not match:
        return [source_url]

    arxiv_id = match.group("arxiv_id")
    return [
        f"https://arxiv.org/pdf/{arxiv_id}",
        f"https://export.arxiv.org/pdf/{arxiv_id}",
    ]


def _read_pdf_response(response: httpx.Response) -> bytes:
    content_length = response.headers.get("content-length", "").strip()
    if content_length.isdigit() and int(content_length) > MAX_PDF_BYTES:
        raise PDFDownloadError("PDF 文件超过 128 MB，建议下载后从本地上传")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_PDF_BYTES:
            raise PDFDownloadError("PDF 文件超过 128 MB，建议下载后从本地上传")
        chunks.append(chunk)
    payload = b"".join(chunks)
    if b"%PDF-" not in payload[:1024]:
        content_type = response.headers.get("content-type", "未知类型").split(";", 1)[0]
        raise PDFDownloadError(f"远程地址返回的不是 PDF（{content_type}）")
    return payload


def download_pdf_bytes(
    source_url: str,
    *,
    client_factory: Callable[..., httpx.Client] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bytes:
    """Download a PDF with retries and arXiv host failover.

    A new HTTP/1.1 connection is opened for every attempt.  This avoids reusing
    a socket that an upstream server or Windows network filter has already
    closed, the common cause of WinError 10054 during PDF imports.
    """

    make_client = client_factory or httpx.Client
    pause = sleep or time.sleep
    candidates = _candidate_urls(source_url)
    last_error: Exception | None = None

    for candidate in candidates:
        for attempt in range(DOWNLOAD_ATTEMPTS):
            try:
                timeout = httpx.Timeout(75.0, connect=20.0)
                with make_client(
                    follow_redirects=True,
                    timeout=timeout,
                    headers=_HEADERS,
                    trust_env=True,
                    http2=False,
                ) as client:
                    with client.stream("GET", candidate) as response:
                        response.raise_for_status()
                        return _read_pdf_response(response)
            except httpx.HTTPStatusError as error:
                last_error = error
                if error.response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
            except (httpx.TransportError, PDFDownloadError) as error:
                last_error = error

            if attempt < DOWNLOAD_ATTEMPTS - 1:
                pause(_RETRY_DELAYS[attempt])

    host = urlparse(source_url).netloc or "远程站点"
    if isinstance(last_error, PDFDownloadError):
        detail = str(last_error)
    elif isinstance(last_error, httpx.HTTPStatusError):
        detail = f"HTTP {last_error.response.status_code}"
    elif isinstance(last_error, httpx.TimeoutException):
        detail = "连接超时"
    elif isinstance(last_error, httpx.TransportError):
        detail = "连接被远程服务器重置或网络暂时不可用"
    else:
        detail = "未知网络错误"
    raise PDFDownloadError(f"从 {host} 下载失败：{detail}，已自动重试") from last_error
