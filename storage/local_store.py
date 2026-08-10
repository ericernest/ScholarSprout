"""SQLite storage for papers, mode outputs, conversations, and session memory.

The storage boundary deliberately keeps stable relationships in relational
tables and developing pipeline outputs in named, schema-versioned JSON blocks.
It does not persist provider payloads, hidden reasoning, or cross-conversation
user profiles.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


SCHEMA_VERSION = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def _json(value: Any) -> str:
    """Encode only intentional, JSON-compatible content blocks."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class LocalResearchStore:
    """Small SQLite-backed persistence boundary for the current product scope."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the v1 schema. It is safe to call during every application start."""
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS papers (
                    paper_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    authors_json TEXT NOT NULL DEFAULT '[]',
                    abstract TEXT,
                    publication_year INTEGER,
                    venue TEXT,
                    doi TEXT UNIQUE,
                    arxiv_id TEXT UNIQUE,
                    source_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_files (
                    paper_file_id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
                    file_kind TEXT NOT NULL CHECK(file_kind IN ('pdf', 'extracted_text', 'figure')),
                    storage_uri TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(paper_id, file_kind, sha256)
                );

                CREATE TABLE IF NOT EXISTS paper_documents (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(paper_id) ON DELETE CASCADE,
                    content_schema_version TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_knowledge_graphs (
                    graph_id TEXT PRIMARY KEY,
                    paper_id TEXT REFERENCES papers(paper_id) ON DELETE CASCADE,
                    graph_scope TEXT NOT NULL CHECK(graph_scope IN ('paper', 'cross_paper')),
                    graph_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(paper_id, graph_scope)
                );

                CREATE TABLE IF NOT EXISTS paper_folders (
                    folder_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE,
                    parent_folder_id TEXT REFERENCES paper_folders(folder_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_tags (
                    tag_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS library_items (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(paper_id) ON DELETE CASCADE,
                    reading_status TEXT NOT NULL CHECK(reading_status IN ('unread', 'reading', 'read', 'archived')),
                    note TEXT NOT NULL DEFAULT '',
                    folder_id TEXT REFERENCES paper_folders(folder_id) ON DELETE SET NULL,
                    added_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_tag_links (
                    paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
                    tag_id TEXT NOT NULL REFERENCES paper_tags(tag_id) ON DELETE CASCADE,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(paper_id, tag_id)
                );

                CREATE TABLE IF NOT EXISTS paper_annotations (
                    annotation_id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
                    reading_session_id TEXT REFERENCES paper_reading_sessions(reading_session_id) ON DELETE SET NULL,
                    annotation_type TEXT NOT NULL CHECK(annotation_type IN ('highlight', 'note')),
                    color TEXT NOT NULL CHECK(color IN ('yellow', 'green', 'blue', 'pink')),
                    page_number INTEGER NOT NULL CHECK(page_number > 0),
                    section_id TEXT,
                    selected_text TEXT NOT NULL,
                    anchor_schema_version TEXT NOT NULL,
                    anchor_json TEXT NOT NULL,
                    note_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    user_id TEXT,
                    state TEXT NOT NULL CHECK(state IN ('active', 'closed')),
                    parent_conversation_id TEXT REFERENCES conversations(conversation_id),
                    forked_from_message_id TEXT,
                    created_at TEXT NOT NULL,
                    last_active_at TEXT NOT NULL,
                    closed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    sequence_number INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
                    mode TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, sequence_number)
                );

                CREATE TABLE IF NOT EXISTS work_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS conversation_artifacts (
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL REFERENCES work_artifacts(artifact_id) ON DELETE CASCADE,
                    relation TEXT NOT NULL CHECK(relation IN ('created', 'continued', 'discussed')),
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, artifact_id, relation)
                );

                CREATE TABLE IF NOT EXISTS domain_onboardings (
                    artifact_id TEXT PRIMARY KEY REFERENCES work_artifacts(artifact_id) ON DELETE CASCADE,
                    query TEXT NOT NULL,
                    language TEXT NOT NULL CHECK(language IN ('zh-CN', 'en-US')),
                    current_stage TEXT NOT NULL,
                    output_schema_version TEXT,
                    learner_profile_json TEXT,
                    overview_json TEXT,
                    research_plan_json TEXT,
                    learning_path_json TEXT,
                    quality_json TEXT,
                    knowledge_graph_json TEXT,
                    error_summary TEXT
                );

                CREATE TABLE IF NOT EXISTS domain_recommendations (
                    artifact_id TEXT NOT NULL REFERENCES domain_onboardings(artifact_id) ON DELETE CASCADE,
                    paper_id TEXT NOT NULL REFERENCES papers(paper_id),
                    recommendation_rank INTEGER NOT NULL CHECK(recommendation_rank > 0),
                    paper_role TEXT NOT NULL,
                    reading_priority TEXT NOT NULL,
                    is_canonical INTEGER NOT NULL CHECK(is_canonical IN (0, 1)),
                    reason TEXT NOT NULL,
                    reading_focus_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY(artifact_id, paper_id)
                );

                CREATE TABLE IF NOT EXISTS paper_reading_sessions (
                    reading_session_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL UNIQUE REFERENCES work_artifacts(artifact_id) ON DELETE CASCADE,
                    paper_id TEXT NOT NULL REFERENCES papers(paper_id),
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                    parent_reading_session_id TEXT REFERENCES paper_reading_sessions(reading_session_id),
                    user_id TEXT,
                    fork_context TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK(state IN ('active', 'paused', 'completed')),
                    current_section_id TEXT,
                    current_paragraph_index INTEGER NOT NULL DEFAULT 0 CHECK(current_paragraph_index >= 0),
                    total_sections INTEGER NOT NULL DEFAULT 0 CHECK(total_sections >= 0),
                    active_skills_json TEXT NOT NULL DEFAULT '[]',
                    completed_sections_json TEXT NOT NULL DEFAULT '[]',
                    section_statuses_json TEXT NOT NULL DEFAULT '{}',
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS paper_reading_blocks (
                    reading_block_id TEXT PRIMARY KEY,
                    reading_session_id TEXT NOT NULL REFERENCES paper_reading_sessions(reading_session_id) ON DELETE CASCADE,
                    block_type TEXT NOT NULL,
                    content_schema_version TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    rendered_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(reading_session_id, block_type)
                );

                CREATE TABLE IF NOT EXISTS reading_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    reading_session_id TEXT NOT NULL REFERENCES paper_reading_sessions(reading_session_id) ON DELETE CASCADE,
                    current_section_id TEXT,
                    current_paragraph_index INTEGER NOT NULL DEFAULT 0 CHECK(current_paragraph_index >= 0),
                    active_skills_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_graph_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_memory_snapshots (
                    memory_snapshot_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    through_message_id TEXT REFERENCES messages(message_id),
                    current_goal TEXT NOT NULL DEFAULT '',
                    confirmed_decisions_json TEXT NOT NULL DEFAULT '[]',
                    open_questions_json TEXT NOT NULL DEFAULT '[]',
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_sequence
                    ON messages(conversation_id, sequence_number);
                CREATE INDEX IF NOT EXISTS idx_artifacts_kind_state
                    ON work_artifacts(artifact_kind, state, updated_at);
                CREATE INDEX IF NOT EXISTS idx_reading_sessions_paper
                    ON paper_reading_sessions(paper_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_annotations_paper_page
                    ON paper_annotations(paper_id, page_number, created_at);
                CREATE INDEX IF NOT EXISTS idx_paper_tag_links_tag
                    ON paper_tag_links(tag_id, paper_id);
                CREATE INDEX IF NOT EXISTS idx_memory_snapshots_conversation
                    ON conversation_memory_snapshots(conversation_id, created_at DESC);
                """
            )
            self._ensure_column(connection, "conversations", "user_id", "TEXT")
            self._ensure_column(
                connection, "messages", "mode", "TEXT NOT NULL DEFAULT 'chat'"
            )
            self._ensure_column(
                connection, "messages", "channel", "TEXT NOT NULL DEFAULT 'web'"
            )
            self._ensure_column(connection, "paper_reading_sessions", "user_id", "TEXT")
            self._migrate_folder_name_uniqueness(connection)
            self._ensure_column(
                connection,
                "library_items",
                "folder_id",
                "TEXT REFERENCES paper_folders(folder_id) ON DELETE SET NULL",
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_library_items_folder ON library_items(folder_id, updated_at DESC)"
            )
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_folders_parent_name
                   ON paper_folders(COALESCE(parent_folder_id, ''), name COLLATE NOCASE)"""
            )
            self._ensure_column(
                connection,
                "paper_reading_sessions",
                "progress_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_versions(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now()),
            )

    @staticmethod
    def _migrate_folder_name_uniqueness(connection: sqlite3.Connection) -> None:
        """Replace v4's global folder-name uniqueness with sibling uniqueness."""
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'paper_folders'"
        ).fetchone()
        definition = str(row["sql"] or "") if row else ""
        if "NAME TEXT NOT NULL UNIQUE" not in definition.upper():
            return
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.executescript(
                """
                CREATE TABLE paper_folders_v5 (
                    folder_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE,
                    parent_folder_id TEXT REFERENCES paper_folders_v5(folder_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO paper_folders_v5(folder_id, name, parent_folder_id, created_at, updated_at)
                    SELECT folder_id, name, parent_folder_id, created_at, updated_at FROM paper_folders;
                DROP TABLE paper_folders;
                ALTER TABLE paper_folders_v5 RENAME TO paper_folders;
                """
            )
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    def upsert_paper(
        self,
        *,
        title: str,
        authors: list[str],
        abstract: str | None = None,
        publication_year: int | None = None,
        venue: str | None = None,
        doi: str | None = None,
        arxiv_id: str | None = None,
        source_url: str | None = None,
        paper_id: str | None = None,
    ) -> str:
        """Store one canonical paper record; DOI and arXiv IDs prevent duplicates."""
        if not title.strip():
            raise ValueError("title must not be empty")
        now = _now()
        paper_id = paper_id or _id("paper")
        with self._connection() as connection:
            existing = connection.execute(
                """SELECT paper_id FROM papers WHERE paper_id = ?
                   OR (doi IS NOT NULL AND doi = ?)
                   OR (arxiv_id IS NOT NULL AND arxiv_id = ?)""",
                (paper_id, doi, arxiv_id),
            ).fetchone()
            if existing:
                paper_id = str(existing["paper_id"])
                authors_json = _json(authors)
                connection.execute(
                    """UPDATE papers SET title = ?,
                       authors_json = CASE WHEN ? = '[]' THEN authors_json ELSE ? END,
                       abstract = COALESCE(?, abstract), publication_year = COALESCE(?, publication_year),
                       venue = COALESCE(?, venue), doi = COALESCE(?, doi), arxiv_id = COALESCE(?, arxiv_id),
                       source_url = COALESCE(?, source_url), updated_at = ? WHERE paper_id = ?""",
                    (
                        title,
                        authors_json,
                        authors_json,
                        abstract,
                        publication_year,
                        venue,
                        doi,
                        arxiv_id,
                        source_url,
                        now,
                        paper_id,
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO papers(paper_id, title, authors_json, abstract, publication_year, venue, doi, arxiv_id, source_url, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (paper_id, title, _json(authors), abstract, publication_year, venue, doi, arxiv_id, source_url, now, now),
                )
        return paper_id

    def add_to_library(
        self,
        paper_id: str,
        *,
        reading_status: str = "unread",
        note: str = "",
        folder_id: str | None = None,
    ) -> None:
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO library_items(paper_id, reading_status, note, folder_id, added_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(paper_id) DO UPDATE SET reading_status = excluded.reading_status,
                   note = excluded.note, folder_id = excluded.folder_id,
                   updated_at = excluded.updated_at""",
                (paper_id, reading_status, note, folder_id, now, now),
            )

    def ensure_library_item(self, paper_id: str, *, reading_status: str = "unread") -> None:
        """Add pipeline-created papers without overwriting user-managed notes or folders."""
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO library_items(
                   paper_id, reading_status, note, folder_id, added_at, updated_at)
                   VALUES (?, ?, '', NULL, ?, ?)""",
                (paper_id, reading_status, now, now),
            )
            if reading_status == "reading":
                connection.execute(
                    """UPDATE library_items SET reading_status = 'reading', updated_at = ?
                       WHERE paper_id = ? AND reading_status = 'unread'""",
                    (now, paper_id),
                )

    def ensure_paper_reference(self, paper_id: str, *, title: str) -> None:
        """Create a minimal FK target without overwriting parsed paper metadata."""
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO papers(
                   paper_id, title, authors_json, created_at, updated_at)
                   VALUES (?, ?, '[]', ?, ?)""",
                (paper_id, title or paper_id, now, now),
            )

    def save_paper_document(
        self,
        paper_id: str,
        document: dict[str, Any],
        *,
        content_schema_version: str = "paper-document-v1",
    ) -> None:
        """Persist the evolving parsed-paper payload beside normalized paper fields."""
        self.upsert_paper(
            paper_id=paper_id,
            title=str(document.get("title") or paper_id),
            authors=[str(author) for author in document.get("authors", [])],
            abstract=str(document.get("abstract") or "") or None,
            publication_year=document.get("year"),
            venue=str(document.get("venue") or "") or None,
            doi=str(document.get("doi") or "") or None,
            arxiv_id=str(document.get("arxiv_id") or "") or None,
            source_url=str(document.get("url") or document.get("pdf_url") or "") or None,
        )
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO paper_documents(paper_id, content_schema_version, document_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(paper_id) DO UPDATE SET content_schema_version = excluded.content_schema_version,
                   document_json = excluded.document_json, updated_at = excluded.updated_at""",
                (paper_id, content_schema_version, _json(document), _now()),
            )

    def load_paper_document(self, paper_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM paper_documents WHERE paper_id = ?", (paper_id,)
            ).fetchone()
        return json.loads(row["document_json"]) if row else None

    def list_paper_documents(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT document_json FROM paper_documents ORDER BY updated_at DESC"
            ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]

    def delete_paper(self, paper_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
        return cursor.rowcount > 0

    def save_paper_file(
        self,
        paper_id: str,
        *,
        file_kind: str,
        storage_uri: str,
        sha256: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO paper_files(paper_file_id, paper_id, file_kind, storage_uri, sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_id("paper_file"), paper_id, file_kind, storage_uri, sha256, _now()),
            )

    def find_paper_by_file_hash(self, sha256: str, *, file_kind: str = "pdf") -> str | None:
        """Return the canonical paper already backed by the same binary file."""
        if not sha256.strip():
            return None
        with self._connection() as connection:
            row = connection.execute(
                """SELECT paper_id FROM paper_files
                   WHERE file_kind = ? AND sha256 = ? ORDER BY created_at LIMIT 1""",
                (file_kind, sha256),
            ).fetchone()
        return str(row["paper_id"]) if row else None

    def find_paper_by_identity(
        self,
        *,
        arxiv_id: str | None = None,
        doi: str | None = None,
        source_url: str | None = None,
    ) -> str | None:
        """Resolve stable external identities before importing another PDF copy."""
        if not any((arxiv_id, doi, source_url)):
            return None
        with self._connection() as connection:
            row = connection.execute(
                """SELECT paper_id FROM papers WHERE
                   (? IS NOT NULL AND arxiv_id = ?) OR
                   (? IS NOT NULL AND doi = ?) OR
                   (? IS NOT NULL AND source_url = ?)
                   ORDER BY updated_at DESC LIMIT 1""",
                (arxiv_id, arxiv_id, doi, doi, source_url, source_url),
            ).fetchone()
        return str(row["paper_id"]) if row else None

    def save_paper_knowledge_graph(
        self,
        graph: dict[str, Any],
        *,
        paper_id: str | None = None,
    ) -> None:
        scope = "paper" if paper_id else "cross_paper"
        graph_id = f"paper:{paper_id}" if paper_id else "cross_paper"
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO paper_knowledge_graphs(graph_id, paper_id, graph_scope, graph_json, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(graph_id) DO UPDATE SET graph_json = excluded.graph_json, updated_at = excluded.updated_at""",
                (graph_id, paper_id, scope, _json(graph), _now()),
            )

    def load_paper_knowledge_graph(self, *, paper_id: str | None = None) -> dict[str, Any] | None:
        graph_id = f"paper:{paper_id}" if paper_id else "cross_paper"
        with self._connection() as connection:
            row = connection.execute(
                "SELECT graph_json FROM paper_knowledge_graphs WHERE graph_id = ?", (graph_id,)
            ).fetchone()
        return json.loads(row["graph_json"]) if row else None

    def ensure_conversation(
        self,
        conversation_id: str,
        *,
        title: str,
        user_id: str | None = None,
        parent_conversation_id: str | None = None,
        forked_from_message_id: str | None = None,
    ) -> str:
        if not conversation_id.strip():
            raise ValueError("conversation_id must not be empty")
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO conversations(conversation_id, title, user_id, state, parent_conversation_id,
                   forked_from_message_id, created_at, last_active_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                   user_id = COALESCE(excluded.user_id, conversations.user_id), last_active_at = excluded.last_active_at""",
                (conversation_id, title or "新会话", user_id, parent_conversation_id, forked_from_message_id, now, now),
            )
        return conversation_id

    def create_conversation(
        self,
        title: str,
        *,
        parent_conversation_id: str | None = None,
        forked_from_message_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        conversation_id = _id("conversation")
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO conversations(conversation_id, title, user_id, state, parent_conversation_id, forked_from_message_id, created_at, last_active_at)
                   VALUES (?, ?, ?, 'active', ?, ?, ?, ?)""",
                (conversation_id, title, user_id, parent_conversation_id, forked_from_message_id, now, now),
            )
        return conversation_id

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        mode: str = "chat",
        channel: str = "web",
        message_id: str | None = None,
    ) -> str:
        if not content.strip():
            raise ValueError("content must not be empty")
        message_id = message_id or _id("message")
        now = _now()
        with self._connection() as connection:
            sequence_number = connection.execute(
                "SELECT COALESCE(MAX(sequence_number), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT OR IGNORE INTO messages(message_id, conversation_id, sequence_number, role, mode, channel, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, conversation_id, sequence_number, role, mode, channel, content, now),
            )
            connection.execute(
                "UPDATE conversations SET last_active_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )
        return message_id

    def create_domain_onboarding(
        self,
        *,
        title: str,
        query: str,
        language: str = "zh-CN",
        current_stage: str = "queued",
        conversation_id: str | None = None,
        artifact_id: str | None = None,
    ) -> str:
        artifact_id = self._create_artifact(
            "domain_onboarding", title, "queued", artifact_id=artifact_id
        )
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO domain_onboardings(artifact_id, query, language, current_stage) VALUES (?, ?, ?, ?)",
                (artifact_id, query, language, current_stage),
            )
            if conversation_id:
                self._link_artifact(connection, conversation_id, artifact_id, "created")
        return artifact_id

    def update_domain_onboarding_state(
        self,
        artifact_id: str,
        *,
        state: str,
        current_stage: str,
        error_summary: str | None = None,
    ) -> None:
        now = _now()
        completed_at = now if state in {"completed", "failed", "cancelled"} else None
        with self._connection() as connection:
            connection.execute(
                "UPDATE domain_onboardings SET current_stage = ?, error_summary = COALESCE(?, error_summary) WHERE artifact_id = ?",
                (current_stage, error_summary, artifact_id),
            )
            connection.execute(
                "UPDATE work_artifacts SET state = ?, updated_at = ?, completed_at = ? WHERE artifact_id = ?",
                (state, now, completed_at, artifact_id),
            )

    def persist_domain_onboarding_result(
        self,
        *,
        query: str,
        response: dict[str, Any],
        conversation_id: str | None = None,
        artifact_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Normalize one pipeline response into stable fields and versioned blocks."""
        if conversation_id:
            self.ensure_conversation(
                conversation_id,
                title=query[:60] or "新会话",
                user_id=user_id,
            )
        if artifact_id is None:
            artifact_id = self.create_domain_onboarding(
                title=f"领域入门：{str(response.get('domain') or query)[:80]}",
                query=query,
                language=str(response.get("language") or "zh-CN"),
                current_stage="completed",
                conversation_id=conversation_id,
            )
        status = str(response.get("status") or "internal_error")
        if status == "cancelled":
            state = "cancelled"
        elif status in {"ok", "quality_warning", "quality_failed"}:
            state = "completed"
        else:
            state = "failed"
        overview_keys = (
            "domain",
            "text",
            "prerequisites",
            "development_stages",
            "current_landscape",
            "evidence_claims",
            "reproducibility",
        )
        overview = {key: response[key] for key in overview_keys if key in response}
        quality = {
            key: response[key]
            for key in ("quality", "quality_attempts", "final_quality", "repair_record")
            if key in response
        }
        self.save_domain_blocks(
            artifact_id,
            output_schema_version=str(
                response.get("schema_version") or "domain-onboarding-output-unknown"
            ),
            learner_profile=response.get("learner_profile"),
            overview=overview or None,
            research_plan=response.get("research_plan"),
            learning_path=response.get("learning_path"),
            quality=quality or None,
            knowledge_graph=response.get("knowledge_graph"),
            state=state,
            current_stage="completed" if state == "completed" else status,
            error_summary=str(response.get("error") or "") or None,
        )
        for rank, paper in enumerate(response.get("papers") or [], start=1):
            if not isinstance(paper, dict) or not str(paper.get("title") or "").strip():
                continue
            paper_id = self.upsert_paper(
                paper_id=str(paper.get("paper_id") or "") or None,
                title=str(paper["title"]),
                authors=[str(author) for author in paper.get("authors", [])],
                abstract=str(paper.get("abstract") or "") or None,
                publication_year=paper.get("year"),
                doi=str(paper.get("doi") or "") or None,
                arxiv_id=str(paper.get("arxiv_id") or "") or None,
                source_url=str(paper.get("url") or "") or None,
            )
            self.recommend_paper(
                artifact_id,
                paper_id,
                recommendation_rank=rank,
                paper_role=str(paper.get("paper_role") or "other"),
                reading_priority=str(paper.get("reading_priority") or "optional"),
                is_canonical=bool(paper.get("is_canonical")),
                reason=str(paper.get("contribution") or ""),
                reading_focus=[str(item) for item in paper.get("reading_focus", [])],
            )
        return artifact_id

    def save_domain_blocks(
        self,
        artifact_id: str,
        *,
        output_schema_version: str,
        learner_profile: dict[str, Any] | None = None,
        overview: dict[str, Any] | None = None,
        research_plan: dict[str, Any] | None = None,
        learning_path: list[dict[str, Any]] | None = None,
        quality: dict[str, Any] | None = None,
        knowledge_graph: dict[str, Any] | None = None,
        state: str = "completed",
        current_stage: str = "completed",
        error_summary: str | None = None,
    ) -> None:
        now = _now()
        completed_at = now if state in {"completed", "failed", "cancelled"} else None
        with self._connection() as connection:
            connection.execute(
                """UPDATE domain_onboardings SET current_stage = ?, output_schema_version = ?, learner_profile_json = ?,
                   overview_json = ?, research_plan_json = ?, learning_path_json = ?, quality_json = ?, knowledge_graph_json = ?, error_summary = ?
                   WHERE artifact_id = ?""",
                (current_stage, output_schema_version, _json(learner_profile) if learner_profile is not None else None,
                 _json(overview) if overview is not None else None, _json(research_plan) if research_plan is not None else None,
                 _json(learning_path) if learning_path is not None else None, _json(quality) if quality is not None else None,
                 _json(knowledge_graph) if knowledge_graph is not None else None, error_summary, artifact_id),
            )
            connection.execute(
                "UPDATE work_artifacts SET state = ?, updated_at = ?, completed_at = ? WHERE artifact_id = ?",
                (state, now, completed_at, artifact_id),
            )

    def recommend_paper(
        self,
        artifact_id: str,
        paper_id: str,
        *,
        recommendation_rank: int,
        paper_role: str,
        reading_priority: str,
        is_canonical: bool,
        reason: str,
        reading_focus: list[str],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO domain_recommendations(artifact_id, paper_id, recommendation_rank, paper_role, reading_priority, is_canonical, reason, reading_focus_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(artifact_id, paper_id) DO UPDATE SET recommendation_rank = excluded.recommendation_rank,
                   paper_role = excluded.paper_role, reading_priority = excluded.reading_priority, is_canonical = excluded.is_canonical,
                   reason = excluded.reason, reading_focus_json = excluded.reading_focus_json""",
                (artifact_id, paper_id, recommendation_rank, paper_role, reading_priority, int(is_canonical), reason, _json(reading_focus)),
            )

    def create_reading_session(
        self,
        *,
        title: str,
        paper_id: str,
        conversation_id: str,
        parent_reading_session_id: str | None = None,
        fork_context: str = "",
    ) -> str:
        artifact_id = self._create_artifact("paper_reading", title, "running")
        reading_session_id = _id("reading")
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO paper_reading_sessions(reading_session_id, artifact_id, paper_id, conversation_id, parent_reading_session_id,
                   fork_context, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (reading_session_id, artifact_id, paper_id, conversation_id, parent_reading_session_id, fork_context, now, now),
            )
            self._link_artifact(connection, conversation_id, artifact_id, "created")
        return reading_session_id

    def save_reading_state(
        self,
        reading_session_id: str,
        *,
        state: str,
        current_section_id: str | None,
        current_paragraph_index: int,
        total_sections: int,
        active_skills: list[str],
        completed_sections: list[str],
        section_statuses: dict[str, str],
    ) -> None:
        now = _now()
        completed_at = now if state == "completed" else None
        artifact_state = {"active": "running", "paused": "paused", "completed": "completed"}[state]
        progress = {
            "current_position": {
                "section_id": current_section_id or "",
                "paragraph_index": current_paragraph_index,
            },
            "total_sections": total_sections,
            "completed_sections": completed_sections,
            "section_statuses": section_statuses,
            "percentage": (
                len(completed_sections) / max(total_sections, 1) * 100
                if total_sections
                else 0.0
            ),
        }
        with self._connection() as connection:
            connection.execute(
                """UPDATE paper_reading_sessions SET state = ?, current_section_id = ?, current_paragraph_index = ?, total_sections = ?,
                   active_skills_json = ?, completed_sections_json = ?, section_statuses_json = ?, progress_json = ?, updated_at = ?, completed_at = ?
                   WHERE reading_session_id = ?""",
                (state, current_section_id, current_paragraph_index, total_sections, _json(active_skills), _json(completed_sections),
                 _json(section_statuses), _json(progress), now, completed_at, reading_session_id),
            )
            connection.execute(
                """UPDATE work_artifacts SET state = ?, updated_at = ?, completed_at = ?
                   WHERE artifact_id = (SELECT artifact_id FROM paper_reading_sessions WHERE reading_session_id = ?)""",
                (artifact_state, now, completed_at, reading_session_id),
            )

    def save_reading_block(
        self,
        reading_session_id: str,
        *,
        block_type: str,
        content_schema_version: str,
        content: dict[str, Any],
        rendered_text: str = "",
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO paper_reading_blocks(reading_block_id, reading_session_id, block_type, content_schema_version, content_json, rendered_text, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(reading_session_id, block_type) DO UPDATE SET content_schema_version = excluded.content_schema_version,
                   content_json = excluded.content_json, rendered_text = excluded.rendered_text, created_at = excluded.created_at""",
                (_id("reading_block"), reading_session_id, block_type, content_schema_version, _json(content), rendered_text, _now()),
            )

    def save_reading_session_snapshot(self, session_id: str, data: dict[str, Any]) -> None:
        """Persist the current ReadingSession dataclass without duplicating chat history."""
        paper_id = str(data.get("paper_id") or f"unassigned:{session_id}")
        paper_title = str(data.get("paper_title") or paper_id)
        self.ensure_paper_reference(paper_id, title=paper_title)
        parent_session_id = str(data.get("parent_session_id") or "") or None
        self.ensure_conversation(
            session_id,
            title=f"论文精读：{paper_title[:70]}",
            user_id=str(data.get("user_id") or "") or None,
            parent_conversation_id=parent_session_id,
        )
        artifact_id = f"paper-reading:{session_id}"
        artifact_state = {
            "active": "running",
            "paused": "paused",
            "completed": "completed",
        }[str(data.get("state") or "active")]
        self._create_artifact(
            "paper_reading",
            f"{paper_title} 精读",
            artifact_state,
            artifact_id=artifact_id,
        )
        progress = data.get("progress") if isinstance(data.get("progress"), dict) else {}
        position = progress.get("current_position") if isinstance(progress.get("current_position"), dict) else {}
        state = str(data.get("state") or "active")
        updated_at = str(data.get("updated_at") or _now())
        completed_at = updated_at if state == "completed" else None
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO paper_reading_sessions(
                   reading_session_id, artifact_id, paper_id, conversation_id, parent_reading_session_id,
                   user_id, fork_context, state, current_section_id, current_paragraph_index, total_sections,
                   active_skills_json, completed_sections_json, section_statuses_json, progress_json,
                   created_at, updated_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(reading_session_id) DO UPDATE SET
                   paper_id = excluded.paper_id, parent_reading_session_id = excluded.parent_reading_session_id,
                   user_id = excluded.user_id, fork_context = excluded.fork_context, state = excluded.state,
                   current_section_id = excluded.current_section_id, current_paragraph_index = excluded.current_paragraph_index,
                   total_sections = excluded.total_sections, active_skills_json = excluded.active_skills_json,
                   completed_sections_json = excluded.completed_sections_json, section_statuses_json = excluded.section_statuses_json,
                   progress_json = excluded.progress_json, updated_at = excluded.updated_at, completed_at = excluded.completed_at""",
                (
                    session_id,
                    artifact_id,
                    paper_id,
                    session_id,
                    parent_session_id,
                    str(data.get("user_id") or "") or None,
                    str(data.get("fork_context") or ""),
                    state,
                    str(position.get("section_id") or "") or None,
                    int(position.get("paragraph_index") or 0),
                    int(progress.get("total_sections") or 0),
                    _json(data.get("active_skills") or []),
                    _json(progress.get("completed_sections") or []),
                    _json(progress.get("section_statuses") or {}),
                    _json(progress),
                    str(data.get("created_at") or _now()),
                    updated_at,
                    completed_at,
                ),
            )
            self._link_artifact(connection, session_id, artifact_id, "created")
            connection.execute(
                "DELETE FROM reading_checkpoints WHERE reading_session_id = ?", (session_id,)
            )
            for checkpoint in data.get("checkpoints") or []:
                if not isinstance(checkpoint, dict):
                    continue
                checkpoint_position = checkpoint.get("position") or {}
                connection.execute(
                    """INSERT INTO reading_checkpoints(checkpoint_id, reading_session_id, current_section_id,
                       current_paragraph_index, active_skills_json, knowledge_graph_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(checkpoint.get("checkpoint_id") or _id("checkpoint")),
                        session_id,
                        str(checkpoint_position.get("section_id") or "") or None,
                        int(checkpoint_position.get("paragraph_index") or 0),
                        _json(checkpoint.get("active_skills") or []),
                        _json(checkpoint.get("kg_state_snapshot") or {}),
                        str(checkpoint.get("created_at") or _now()),
                    ),
                )

    def load_reading_session_snapshot(self, session_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM paper_reading_sessions WHERE reading_session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            checkpoints = connection.execute(
                "SELECT * FROM reading_checkpoints WHERE reading_session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
            fork_rows = connection.execute(
                "SELECT reading_session_id FROM paper_reading_sessions WHERE parent_reading_session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return {
            "session_id": row["reading_session_id"],
            "paper_id": row["paper_id"],
            "paper_title": self._paper_title(str(row["paper_id"])),
            "user_id": row["user_id"] or "default",
            "state": row["state"],
            "checkpoints": [
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "position": {
                        "section_id": checkpoint["current_section_id"] or "",
                        "paragraph_index": checkpoint["current_paragraph_index"],
                    },
                    "active_skills": json.loads(checkpoint["active_skills_json"]),
                    "kg_state_snapshot": json.loads(checkpoint["knowledge_graph_json"] or "{}"),
                    "conversation_history": [],
                    "created_at": checkpoint["created_at"],
                }
                for checkpoint in checkpoints
            ],
            "progress": json.loads(row["progress_json"] or "{}"),
            "active_skills": json.loads(row["active_skills_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "parent_session_id": row["parent_reading_session_id"],
            "fork_sessions": [item["reading_session_id"] for item in fork_rows],
            "fork_context": row["fork_context"],
        }

    def list_reading_session_snapshots(self, paper_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if paper_id is None:
                rows = connection.execute(
                    "SELECT reading_session_id FROM paper_reading_sessions ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT reading_session_id FROM paper_reading_sessions WHERE paper_id = ? ORDER BY updated_at DESC",
                    (paper_id,),
                ).fetchall()
        return [
            snapshot
            for row in rows
            if (snapshot := self.load_reading_session_snapshot(str(row["reading_session_id"])))
            is not None
        ]

    def delete_reading_session(self, session_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT artifact_id FROM paper_reading_sessions WHERE reading_session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM work_artifacts WHERE artifact_id = ?", (row["artifact_id"],))
        return True

    def _paper_title(self, paper_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute("SELECT title FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        return str(row["title"]) if row else paper_id

    def save_memory_snapshot(
        self,
        conversation_id: str,
        *,
        summary: str,
        current_goal: str = "",
        confirmed_decisions: list[str] | None = None,
        open_questions: list[str] | None = None,
        through_message_id: str | None = None,
    ) -> str:
        snapshot_id = _id("memory")
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO conversation_memory_snapshots(memory_snapshot_id, conversation_id, through_message_id, current_goal,
                   confirmed_decisions_json, open_questions_json, summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, conversation_id, through_message_id, current_goal, _json(confirmed_decisions or []),
                 _json(open_questions or []), summary, _now()),
            )
        return snapshot_id

    def list_table_names(self) -> set[str]:
        with self._connection() as connection:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {str(row["name"]) for row in rows}

    def _create_artifact(
        self,
        artifact_kind: str,
        title: str,
        state: str,
        *,
        artifact_id: str | None = None,
    ) -> str:
        artifact_id = artifact_id or _id("artifact")
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO work_artifacts(artifact_id, artifact_kind, title, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(artifact_id) DO UPDATE SET title = excluded.title, state = excluded.state, updated_at = excluded.updated_at""",
                (artifact_id, artifact_kind, title, state, now, now),
            )
        return artifact_id

    @staticmethod
    def _link_artifact(connection: sqlite3.Connection, conversation_id: str, artifact_id: str, relation: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO conversation_artifacts(conversation_id, artifact_id, relation, linked_at) VALUES (?, ?, ?, ?)",
            (conversation_id, artifact_id, relation, _now()),
        )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
