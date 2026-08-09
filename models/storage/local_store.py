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


SCHEMA_VERSION = 1


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
        connection = sqlite3.connect(self.database_path)
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

                CREATE TABLE IF NOT EXISTS library_items (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(paper_id) ON DELETE CASCADE,
                    reading_status TEXT NOT NULL CHECK(reading_status IN ('unread', 'reading', 'read', 'archived')),
                    note TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
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
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, sequence_number)
                );

                CREATE TABLE IF NOT EXISTS work_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_kind TEXT NOT NULL CHECK(artifact_kind IN ('domain_onboarding', 'paper_reading')),
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
                    fork_context TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK(state IN ('active', 'paused', 'completed')),
                    current_section_id TEXT,
                    current_paragraph_index INTEGER NOT NULL DEFAULT 0 CHECK(current_paragraph_index >= 0),
                    total_sections INTEGER NOT NULL DEFAULT 0 CHECK(total_sections >= 0),
                    active_skills_json TEXT NOT NULL DEFAULT '[]',
                    completed_sections_json TEXT NOT NULL DEFAULT '[]',
                    section_statuses_json TEXT NOT NULL DEFAULT '{}',
                    reading_map_json TEXT,
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
                CREATE INDEX IF NOT EXISTS idx_memory_snapshots_conversation
                    ON conversation_memory_snapshots(conversation_id, created_at DESC);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_versions(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now()),
            )

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
                "SELECT paper_id FROM papers WHERE (doi IS NOT NULL AND doi = ?) OR (arxiv_id IS NOT NULL AND arxiv_id = ?)",
                (doi, arxiv_id),
            ).fetchone()
            if existing:
                paper_id = str(existing["paper_id"])
                connection.execute(
                    """UPDATE papers SET title = ?, authors_json = ?, abstract = ?, publication_year = ?,
                       venue = ?, doi = ?, arxiv_id = ?, source_url = ?, updated_at = ? WHERE paper_id = ?""",
                    (title, _json(authors), abstract, publication_year, venue, doi, arxiv_id, source_url, now, paper_id),
                )
            else:
                connection.execute(
                    """INSERT INTO papers(paper_id, title, authors_json, abstract, publication_year, venue, doi, arxiv_id, source_url, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (paper_id, title, _json(authors), abstract, publication_year, venue, doi, arxiv_id, source_url, now, now),
                )
        return paper_id

    def add_to_library(self, paper_id: str, *, reading_status: str = "unread", note: str = "") -> None:
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO library_items(paper_id, reading_status, note, added_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(paper_id) DO UPDATE SET reading_status = excluded.reading_status,
                   note = excluded.note, updated_at = excluded.updated_at""",
                (paper_id, reading_status, note, now, now),
            )

    def create_conversation(
        self,
        title: str,
        *,
        parent_conversation_id: str | None = None,
        forked_from_message_id: str | None = None,
    ) -> str:
        conversation_id = _id("conversation")
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO conversations(conversation_id, title, state, parent_conversation_id, forked_from_message_id, created_at, last_active_at)
                   VALUES (?, ?, 'active', ?, ?, ?, ?)""",
                (conversation_id, title, parent_conversation_id, forked_from_message_id, now, now),
            )
        return conversation_id

    def append_message(self, conversation_id: str, *, role: str, content: str) -> str:
        if not content.strip():
            raise ValueError("content must not be empty")
        message_id = _id("message")
        now = _now()
        with self._connection() as connection:
            sequence_number = connection.execute(
                "SELECT COALESCE(MAX(sequence_number), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO messages(message_id, conversation_id, sequence_number, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, conversation_id, sequence_number, role, content, now),
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
    ) -> str:
        artifact_id = self._create_artifact("domain_onboarding", title, "queued")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO domain_onboardings(artifact_id, query, language, current_stage) VALUES (?, ?, ?, ?)",
                (artifact_id, query, language, current_stage),
            )
            if conversation_id:
                self._link_artifact(connection, conversation_id, artifact_id, "created")
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
        with self._connection() as connection:
            connection.execute(
                """UPDATE paper_reading_sessions SET state = ?, current_section_id = ?, current_paragraph_index = ?, total_sections = ?,
                   active_skills_json = ?, completed_sections_json = ?, section_statuses_json = ?, updated_at = ?, completed_at = ?
                   WHERE reading_session_id = ?""",
                (state, current_section_id, current_paragraph_index, total_sections, _json(active_skills), _json(completed_sections),
                 _json(section_statuses), now, completed_at, reading_session_id),
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

    def _create_artifact(self, artifact_kind: str, title: str, state: str) -> str:
        artifact_id = _id("artifact")
        now = _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO work_artifacts(artifact_id, artifact_kind, title, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, artifact_kind, title, state, now, now),
            )
        return artifact_id

    @staticmethod
    def _link_artifact(connection: sqlite3.Connection, conversation_id: str, artifact_id: str, relation: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO conversation_artifacts(conversation_id, artifact_id, relation, linked_at) VALUES (?, ?, ?, ?)",
            (conversation_id, artifact_id, relation, _now()),
        )
