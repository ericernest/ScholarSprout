"""Paper-reading persistence backed by shared SQLite and local binary files."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from storage.local_store import LocalResearchStore


class PaperReadingStorage:
    """Compatibility API consumed by the existing paper-reading pipeline."""

    def __init__(
        self,
        base_dir: Path | None = None,
        research_store: LocalResearchStore | None = None,
    ) -> None:
        if base_dir is None:
            base_dir = Path.home() / ".novicesynapse" / "paper_reading"
        self.base_dir = Path(base_dir)
        self.research_store = research_store or LocalResearchStore(
            self.base_dir.parent / "research.sqlite3"
        )
        self.research_store.initialize()
        for subdir in ("uploads", "figures"):
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)

    def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        self.research_store.save_reading_session_snapshot(session_id, data)

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        return self.research_store.load_reading_session_snapshot(session_id)

    def delete_session(self, session_id: str) -> bool:
        return self.research_store.delete_reading_session(session_id)

    def list_sessions(self, paper_id: str | None = None) -> list[dict[str, Any]]:
        return self.research_store.list_reading_session_snapshots(paper_id)

    def save_paper(self, paper_id: str, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload["paper_id"] = paper_id
        self.research_store.save_paper_document(paper_id, payload)
        upload = self.get_upload_path(paper_id)
        if upload is not None:
            self.research_store.save_paper_file(
                paper_id,
                file_kind="pdf",
                storage_uri=upload.resolve().as_uri(),
                sha256=self._sha256(upload),
            )

    def load_paper(self, paper_id: str) -> dict[str, Any] | None:
        return self.research_store.load_paper_document(paper_id)

    def delete_paper(self, paper_id: str) -> bool:
        deleted = self.research_store.delete_paper(paper_id)
        self.delete_upload(paper_id)
        figures_dir = self.base_dir / "figures" / self._safe_component(paper_id)
        if figures_dir.exists():
            for path in figures_dir.iterdir():
                if path.is_file():
                    path.unlink()
            figures_dir.rmdir()
        return deleted

    def list_papers(self) -> list[dict[str, Any]]:
        return [
            {
                "paper_id": data.get("paper_id", ""),
                "title": data.get("title", ""),
                "authors": data.get("authors", []),
                "source": data.get("source", ""),
                "venue": data.get("venue", ""),
                "year": data.get("year"),
                "sections_count": len(data.get("sections", [])),
                "stored_at": data.get("stored_at", ""),
            }
            for data in self.research_store.list_paper_documents()
        ]

    def save_kg(self, paper_id: str, kg_data: dict[str, Any]) -> None:
        self.research_store.save_paper_knowledge_graph(kg_data, paper_id=paper_id)

    def load_kg(self, paper_id: str) -> dict[str, Any] | None:
        return self.research_store.load_paper_knowledge_graph(paper_id=paper_id)

    def save_cross_paper_kg(self, kg_data: dict[str, Any]) -> None:
        self.research_store.save_paper_knowledge_graph(kg_data)

    def load_cross_paper_kg(self) -> dict[str, Any] | None:
        return self.research_store.load_paper_knowledge_graph()

    def save_upload(self, paper_id: str, pdf_bytes: bytes) -> Path:
        path = self.base_dir / "uploads" / f"{self._safe_component(paper_id)}.pdf"
        path.write_bytes(pdf_bytes)
        return path

    def get_upload_path(self, paper_id: str) -> Path | None:
        path = self.base_dir / "uploads" / f"{self._safe_component(paper_id)}.pdf"
        return path if path.is_file() else None

    def delete_upload(self, paper_id: str) -> bool:
        path = self.base_dir / "uploads" / f"{self._safe_component(paper_id)}.pdf"
        if not path.exists():
            return False
        path.unlink()
        return True

    def save_figure(self, paper_id: str, asset_name: str, image_bytes: bytes) -> Path:
        safe_paper_id = self._safe_component(paper_id)
        safe_name = self._safe_component(asset_name)
        directory = self.base_dir / "figures" / safe_paper_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / safe_name
        path.write_bytes(image_bytes)
        return path

    def get_figure_path(self, paper_id: str, asset_name: str) -> Path | None:
        try:
            safe_paper_id = self._safe_component(paper_id)
            safe_name = self._safe_component(asset_name)
        except ValueError:
            return None
        path = self.base_dir / "figures" / safe_paper_id / safe_name
        return path if path.is_file() else None

    def get_storage_stats(self) -> dict[str, Any]:
        sessions = self.research_store.list_reading_session_snapshots()
        papers = self.research_store.list_paper_documents()
        uploads = list((self.base_dir / "uploads").glob("*.pdf"))
        figures = list((self.base_dir / "figures").glob("*/*"))
        total_size = sum(
            path.stat().st_size for path in [*uploads, *figures] if path.is_file()
        )
        return {
            "sessions": len(sessions),
            "papers": len(papers),
            "uploads": len(uploads),
            "figures": len(figures),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "base_dir": str(self.base_dir),
            "database_path": str(self.research_store.database_path),
        }

    @staticmethod
    def _safe_component(value: str) -> str:
        raw = str(value or "")
        if not raw or raw in {".", ".."} or "/" in raw or "\\" in raw:
            raise ValueError("Invalid storage path component.")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip(" ._")
        if not safe:
            safe = "paper"
        if safe != raw:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
            safe = f"{safe[:80]}-{digest}"
        return safe

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
