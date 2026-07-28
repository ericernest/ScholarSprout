"""论文精读本地存储 — JSON 文件持久化。

存储路径: ~/.novicesynapse/paper_reading/
├── sessions/{session_id}.json     # 会话状态 + checkpoints
├── papers/{paper_id}.json         # 论文元数据 + 解析后的结构化内容
├── kg/{paper_id}_kg.json          # 单篇论文 KG 快照
└── uploads/{paper_id}.pdf         # 用户上传的 PDF 原文
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PaperReadingStorage:
    """本地 JSON 文件持久化。

    设计原则:
    - 与项目「本地优先」理念一致
    - 无外部数据库依赖
    - 所有数据以 utf-8 编码的 JSON 文件存储
    - 上传的 PDF 以二进制方式存储
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        """初始化存储。

        Args:
            base_dir: 存储根目录，默认为 ~/.novicesynapse/paper_reading/
        """
        if base_dir is None:
            base_dir = Path.home() / ".novicesynapse" / "paper_reading"
        self.base_dir = Path(base_dir)

        # 确保子目录存在
        for subdir in ("sessions", "papers", "kg", "uploads", "figures"):
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)

    # ── Session 操作 ──

    def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        """保存会话状态到文件。

        覆盖已存在的会话文件。
        """
        path = self.base_dir / "sessions" / f"{session_id}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        """从文件加载会话状态。

        Returns:
            会话数据字典，如果文件不存在则返回 None。
        """
        path = self.base_dir / "sessions" / f"{session_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def delete_session(self, session_id: str) -> bool:
        """删除会话文件。

        Returns:
            True 如果成功删除，False 如果文件不存在。
        """
        path = self.base_dir / "sessions" / f"{session_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_sessions(self, paper_id: str | None = None) -> list[dict[str, Any]]:
        """列出所有会话（可按论文 ID 过滤）。

        Args:
            paper_id: 可选，筛选指定论文的会话。

        Returns:
            会话数据字典列表，按 updated_at 降序排列。
        """
        sessions_dir = self.base_dir / "sessions"
        if not sessions_dir.exists():
            return []

        sessions = []
        for path in sorted(
            sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if paper_id is None or data.get("paper_id") == paper_id:
                    sessions.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return sessions

    # ── Paper 操作 ──

    def save_paper(self, paper_id: str, data: dict[str, Any]) -> None:
        """保存论文元数据和结构化内容。"""
        path = self.base_dir / "papers" / f"{paper_id}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def load_paper(self, paper_id: str) -> dict[str, Any] | None:
        """加载论文数据。"""
        path = self.base_dir / "papers" / f"{paper_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def delete_paper(self, paper_id: str) -> bool:
        """删除论文及其关联数据（KG + 上传文件）。"""
        path = self.base_dir / "papers" / f"{paper_id}.json"
        deleted = False
        if path.exists():
            path.unlink()
            deleted = True

        # 同时清理 KG 和上传文件
        kg_path = self.base_dir / "kg" / f"{paper_id}_kg.json"
        if kg_path.exists():
            kg_path.unlink()

        upload_path = self.base_dir / "uploads" / f"{paper_id}.pdf"
        if upload_path.exists():
            upload_path.unlink()

        figures_dir = self.base_dir / "figures" / paper_id
        if figures_dir.exists():
            for figure_path in figures_dir.iterdir():
                if figure_path.is_file():
                    figure_path.unlink()
            figures_dir.rmdir()

        return deleted

    def list_papers(self) -> list[dict[str, Any]]:
        """列出所有已存储的论文摘要。"""
        papers_dir = self.base_dir / "papers"
        if not papers_dir.exists():
            return []

        papers = []
        for path in papers_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # 只返回摘要信息，不返回完整 sections 内容
                papers.append({
                    "paper_id": data.get("paper_id", ""),
                    "title": data.get("title", ""),
                    "authors": data.get("authors", []),
                    "source": data.get("source", ""),
                    "venue": data.get("venue", ""),
                    "year": data.get("year"),
                    "sections_count": len(data.get("sections", [])),
                    "stored_at": data.get("stored_at", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return papers

    # ── KG 操作 ──

    def save_kg(self, paper_id: str, kg_data: dict[str, Any]) -> None:
        """保存论文知识图谱快照。"""
        path = self.base_dir / "kg" / f"{paper_id}_kg.json"
        path.write_text(
            json.dumps(kg_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def load_kg(self, paper_id: str) -> dict[str, Any] | None:
        """加载论文知识图谱快照。"""
        path = self.base_dir / "kg" / f"{paper_id}_kg.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_cross_paper_kg(self, kg_data: dict[str, Any]) -> None:
        """保存跨论文融合后的 KG。"""
        path = self.base_dir / "kg" / "cross_paper_kg.json"
        path.write_text(
            json.dumps(kg_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def load_cross_paper_kg(self) -> dict[str, Any] | None:
        """加载跨论文融合 KG。"""
        path = self.base_dir / "kg" / "cross_paper_kg.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ── Upload 操作 ──

    def save_upload(self, paper_id: str, pdf_bytes: bytes) -> Path:
        """保存用户上传的 PDF 文件。

        Args:
            paper_id: 论文内部 ID
            pdf_bytes: PDF 文件的原始字节

        Returns:
            保存后的文件路径。
        """
        path = self.base_dir / "uploads" / f"{paper_id}.pdf"
        path.write_bytes(pdf_bytes)
        return path

    def get_upload_path(self, paper_id: str) -> Path | None:
        """获取上传 PDF 的路径。

        Returns:
            文件路径，如果文件不存在则返回 None。
        """
        path = self.base_dir / "uploads" / f"{paper_id}.pdf"
        return path if path.exists() else None

    def delete_upload(self, paper_id: str) -> bool:
        """删除上传的 PDF 文件。"""
        path = self.base_dir / "uploads" / f"{paper_id}.pdf"
        if not path.exists():
            return False
        path.unlink()
        return True

    # ── Figure operations ──

    def save_figure(self, paper_id: str, asset_name: str, image_bytes: bytes) -> Path:
        """Persist an extracted paper figure under a paper-scoped directory."""
        safe_name = Path(asset_name).name
        safe_paper_id = Path(paper_id).name
        if (
            not safe_name
            or safe_name != asset_name
            or not safe_paper_id
            or safe_paper_id != paper_id
            or safe_paper_id in {".", ".."}
        ):
            raise ValueError("Invalid figure asset name.")
        directory = self.base_dir / "figures" / safe_paper_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / safe_name
        path.write_bytes(image_bytes)
        return path

    def get_figure_path(self, paper_id: str, asset_name: str) -> Path | None:
        """Return a previously extracted figure without allowing path traversal."""
        safe_name = Path(asset_name).name
        safe_paper_id = Path(paper_id).name
        if (
            not safe_name
            or safe_name != asset_name
            or not safe_paper_id
            or safe_paper_id != paper_id
            or safe_paper_id in {".", ".."}
        ):
            return None
        path = self.base_dir / "figures" / safe_paper_id / safe_name
        return path if path.is_file() else None

    # ── 批量操作 ──

    def get_storage_stats(self) -> dict[str, Any]:
        """获取存储统计信息。"""
        sessions_count = len(list((self.base_dir / "sessions").glob("*.json")))
        papers_count = len(list((self.base_dir / "papers").glob("*.json")))
        kg_count = len(list((self.base_dir / "kg").glob("*.json")))
        uploads_count = len(list((self.base_dir / "uploads").glob("*.pdf")))
        figures_count = len(list((self.base_dir / "figures").glob("*/*")))

        total_size = sum(
            p.stat().st_size
            for subdir in ("sessions", "papers", "kg", "uploads", "figures")
            for p in (self.base_dir / subdir).rglob("*")
            if p.is_file()
        )

        return {
            "sessions": sessions_count,
            "papers": papers_count,
            "kg_files": kg_count,
            "uploads": uploads_count,
            "figures": figures_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "base_dir": str(self.base_dir),
        }
