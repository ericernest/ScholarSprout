"""Functional verification for the paper_reading module.

This script exercises the real local PDF parser, core handler actions, skill
loading, fork flow, and skill post-processing without calling an
external LLM. It is intended as a repeatable handoff/smoke test.
"""

from __future__ import annotations

import base64
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

if "fastapi" not in sys.modules:
    fastapi_stub = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class Request:
        pass

    fastapi_stub.HTTPException = HTTPException
    fastapi_stub.Request = Request
    sys.modules["fastapi"] = fastapi_stub

if "feedparser" not in sys.modules:
    feedparser_stub = types.ModuleType("feedparser")
    feedparser_stub.parse = lambda text: SimpleNamespace(entries=[])
    sys.modules["feedparser"] = feedparser_stub

import handlers.paper_reading.handler as handler_mod
from channels.base import ChannelMessage
from handlers.paper_reading.handler import handle_paper_reading_message
from handlers.paper_reading.harness.fork_merge import ForkMergeManager
from handlers.paper_reading.harness.session import SessionManager
from handlers.paper_reading.harness.storage import PaperReadingStorage
from handlers.paper_reading.postprocessors.postprocess import postprocess_agent_output
from runtime.agent_runner import AgentRunResult, TokenUsage
from skills.registry import create_skill_registry
from skills.selector import CapabilitySelector
from tools.registry import create_builtin_tool_registry


def _message(content: dict) -> ChannelMessage:
    return ChannelMessage(
        session_id="verify-session",
        channel="web",
        direction="inbound",
        mode="paper_reading",
        content=content,
    )


def _fake_run_agent_detailed(
    agent,
    user_content: str,
    tool_registry,
    skill_registry=None,
    capability_selector=None,
    max_steps: int = 5,
) -> AgentRunResult:
    print("agent_context_len", len(user_content))
    print("agent_context_has_section", "[当前章节正文]" in user_content)
    text = (
        '{"method_summary":"module method",'
        '"assumptions":["pdf parsed"],'
        '"steps":[{"name":"Step 1","description":"extract section"}],'
        '"risks":["pdf text layer required"],'
        '"questions":["need formula check?"]}'
    )
    return AgentRunResult(
        text=text,
        duration_ms=1.23,
        usage=TokenUsage(),
        model_calls=1,
    )


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    pdf_path = root / "docs" / "reference" / "paper-reading" / "论文精读.pdf"
    print("pdf_exists", pdf_path.exists(), "size", pdf_path.stat().st_size if pdf_path.exists() else 0)
    assert pdf_path.exists(), f"missing sample PDF: {pdf_path}"

    base_dir = Path(tempfile.mkdtemp(prefix="paper-reading-verify-"))
    storage = PaperReadingStorage(base_dir=base_dir)
    session_manager = SessionManager(storage=storage)
    fork_manager = ForkMergeManager(session_manager)

    class DummyAgent:
        pass

    pipeline = None
    pdf_runtime_ready = True
    try:
        import fitz  # noqa: F401
        from handlers.paper_reading.pipeline.sources import PaperPipeline

        pipeline = PaperPipeline()
    except ModuleNotFoundError as error:
        pdf_runtime_ready = False
        print("pdf_parse_skipped_missing_dependency", error.name)

    app_state = SimpleNamespace(
        paper_pipeline=pipeline,
        paper_storage=storage,
        session_manager=session_manager,
        fork_manager=fork_manager,
        paper_reading_agent=DummyAgent(),
        tool_registry=create_builtin_tool_registry(),
        skill_registry=create_skill_registry(),
        capability_selector=CapabilitySelector(),
        model=None,
    )

    handler_mod.run_agent_detailed = _fake_run_agent_detailed

    if pdf_runtime_ready and pipeline is not None:
        metadata = pipeline.parse_pdf(pdf_path)
        print("parse_status", metadata.parse_status)
        print("title", metadata.title[:80])
        print("sections_count", len(metadata.sections))
        print("full_text_len", len(metadata.full_text))
        print("abstract_len", len(metadata.abstract))
        assert metadata.parse_status == "done"
        assert len(metadata.full_text) > 500
        assert len(metadata.sections) >= 1

        pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        upload = handle_paper_reading_message(_message({"action": "upload_paper", "pdf_data": pdf_b64}), app_state)
        print("upload_status", upload.get("status"), "reading_map_status", upload.get("data", {}).get("reading_map_status"))
        assert upload["status"] == "ok"
        paper_id = upload["data"]["paper_id"]
        sections = upload["data"]["sections"]
        assert storage.get_upload_path(paper_id).exists()
        assert upload["data"]["sections_count"] >= 1
    else:
        paper_id = "verify-paper"
        sections = [
            {
                "section_id": "sec:abstract",
                "title": "Abstract",
                "level": 1,
                "content": "This paper studies a verification problem and proposes a modular method.",
                "paragraphs": ["This paper studies a verification problem and proposes a modular method."],
            },
            {
                "section_id": "sec:method",
                "title": "3 Method",
                "level": 1,
                "content": "The method uses a parser, a reading map builder, and skill-specific analysis.",
                "paragraphs": ["The method uses a parser, a reading map builder, and skill-specific analysis."],
            },
        ]
        storage.save_paper(
            paper_id,
            {
                "paper_id": paper_id,
                "source": "synthetic",
                "title": "Synthetic Paper Reading Verification",
                "authors": [{"name": "Verifier"}],
                "abstract": sections[0]["content"],
                "sections": sections,
                "full_text": "\n\n".join(section["content"] for section in sections),
                "parse_status": "synthetic",
            },
        )
        storage.save_upload(paper_id, pdf_path.read_bytes())

    detail = handle_paper_reading_message(_message({"action": "get_paper_detail", "paper_id": paper_id}), app_state)
    print(
        "detail_status",
        detail.get("status"),
        "has_pdf",
        detail.get("data", {}).get("has_pdf"),
        "sections",
        detail.get("data", {}).get("paper", {}).get("sections_count"),
    )
    assert detail["status"] == "ok"
    assert detail["data"]["has_pdf"] is True
    assert detail["data"]["paper"]["full_text"]

    first_section_id = sections[0]["section_id"]
    start = handle_paper_reading_message(
        _message({
            "action": "start_reading",
            "paper_id": paper_id,
            "content": "analyze method derivation",
            "target_section": first_section_id,
        }),
        app_state,
    )
    print(
        "start_status",
        start.get("status"),
        "model_calls",
        start.get("data", {}).get("model_calls"),
        "skills",
        [s.get("skill_id") for s in start.get("skill_outputs", [])],
    )
    assert start["status"] == "ok"
    assert start["data"]["context"]["paper_loaded"] is True
    assert "revealed_kg" not in start["data"]
    assert len(start.get("skill_outputs", [])) >= 1
    session_id = start["data"]["session_id"]

    load = handle_paper_reading_message(
        _message({"action": "load_skill", "session_id": session_id, "skill_ids": ["reading.math_verifier"]}),
        app_state,
    )
    print("load_status", load.get("status"), load.get("data", {}).get("active_skills"))
    assert load["status"] == "ok"

    fork = handle_paper_reading_message(
        _message({
            "action": "fork",
            "session_id": session_id,
            "fork_context": "formula snippet",
            "fork_skills": ["reading.math_verifier"],
            "fork_question": "verify derivation step by step",
        }),
        app_state,
    )
    print("fork_status", fork.get("status"), fork.get("data", {}).get("fork_session_id"))
    assert fork["status"] == "ok"

    pause = handle_paper_reading_message(_message({"action": "pause_reading", "session_id": session_id}), app_state)
    resume = handle_paper_reading_message(_message({"action": "resume_reading", "session_id": session_id}), app_state)
    progress = handle_paper_reading_message(_message({"action": "get_progress", "session_id": session_id}), app_state)
    state = handle_paper_reading_message(_message({"action": "get_session_state", "session_id": session_id}), app_state)
    print("pause_resume_progress_state", pause.get("status"), resume.get("status"), progress.get("status"), state.get("status"))
    assert pause["status"] == resume["status"] == progress["status"] == state["status"] == "ok"

    outs = postprocess_agent_output(
        (
            '{"verification_summary":"formula ok",'
            '"derivation_steps":[{"step":"1","reason":"substitution"}],'
            '"assumptions":["shape matches"],'
            '"issues":[],'
            '"confidence":0.8}'
        ),
        ["reading.math_verifier"],
        paper_id=paper_id,
        section_id="sec:x",
        trigger="fork",
    )
    print("postprocess_skill", outs[0]["skill_id"], outs[0]["output_type"], bool(outs[0]["rendered"]))
    assert outs and outs[0]["skill_id"] == "reading.math_verifier"

    print("FUNCTIONAL_VERIFY_PASSED")


if __name__ == "__main__":
    main()
