from __future__ import annotations

import unittest

import httpx

from handlers.paper_reading.pdf_download import (
    PDFDownloadError,
    _candidate_urls,
    download_pdf_bytes,
)


def client_factory_for(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return factory


class PDFDownloadTests(unittest.TestCase):
    def test_arxiv_urls_have_main_and_export_pdf_candidates(self) -> None:
        self.assertEqual(
            _candidate_urls("https://arxiv.org/abs/2308.11432v2"),
            [
                "https://arxiv.org/pdf/2308.11432v2",
                "https://export.arxiv.org/pdf/2308.11432v2",
            ],
        )

    def test_connection_reset_is_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("[WinError 10054] connection reset", request=request)
            return httpx.Response(200, content=b"%PDF-1.7\nretry worked", request=request)

        payload = download_pdf_bytes(
            "https://example.org/paper.pdf",
            client_factory=client_factory_for(handler),
            sleep=lambda _: None,
        )

        self.assertTrue(payload.startswith(b"%PDF-"))
        self.assertEqual(calls, 2)

    def test_arxiv_falls_back_to_export_host_after_retries(self) -> None:
        requested_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
            if request.url.host == "arxiv.org":
                raise httpx.ConnectError("connection reset", request=request)
            return httpx.Response(200, content=b"%PDF-1.7\nfallback", request=request)

        payload = download_pdf_bytes(
            "https://arxiv.org/pdf/2308.11432.pdf",
            client_factory=client_factory_for(handler),
            sleep=lambda _: None,
        )

        self.assertTrue(payload.startswith(b"%PDF-"))
        self.assertEqual(requested_hosts.count("arxiv.org"), 3)
        self.assertEqual(requested_hosts[-1], "export.arxiv.org")

    def test_html_response_is_not_saved_as_pdf(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html>access denied</html>",
                request=request,
            )

        with self.assertRaisesRegex(PDFDownloadError, "不是 PDF"):
            download_pdf_bytes(
                "https://example.org/paper.pdf",
                client_factory=client_factory_for(handler),
                sleep=lambda _: None,
            )

    def test_non_http_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(PDFDownloadError, "http 或 https"):
            download_pdf_bytes("file:///C:/secret.pdf")


if __name__ == "__main__":
    unittest.main()
