"""Read models and paper-library operations for the local research center."""

from __future__ import annotations

import json
from typing import Any

from .local_store import LocalResearchStore, _json, _now


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class ResearchCatalog:
    """Stable list/detail queries kept separate from pipeline write methods."""

    def __init__(self, store: LocalResearchStore) -> None:
        self.store = store

    def counts(self) -> dict[str, int]:
        with self.store._connection() as connection:
            row = connection.execute(
                """SELECT
                   (SELECT COUNT(*) FROM conversations) AS conversations,
                   (SELECT COUNT(*) FROM domain_onboardings) AS domain_onboardings,
                   (SELECT COUNT(*) FROM paper_reading_sessions) AS paper_readings,
                   (SELECT COUNT(*) FROM library_items) AS library_papers,
                   (SELECT COUNT(*) FROM papers) AS all_papers"""
            ).fetchone()
        return {key: int(row[key]) for key in row.keys()}

    def list_conversations(self, *, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT c.conversation_id, c.title, c.state, c.parent_conversation_id,
                          c.created_at, c.last_active_at, COUNT(m.message_id) AS message_count,
                          GROUP_CONCAT(DISTINCT m.mode) AS modes,
                          (SELECT content FROM messages latest
                           WHERE latest.conversation_id = c.conversation_id
                           ORDER BY latest.sequence_number DESC LIMIT 1) AS latest_message
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.conversation_id
                   WHERE c.title LIKE ? OR EXISTS (
                       SELECT 1 FROM messages found
                       WHERE found.conversation_id = c.conversation_id AND found.content LIKE ?)
                   GROUP BY c.conversation_id ORDER BY c.last_active_at DESC LIMIT ?""",
                (pattern, pattern, limit),
            ).fetchall()
        return [
            {
                "conversation_id": row["conversation_id"],
                "title": row["title"],
                "state": row["state"],
                "parent_conversation_id": row["parent_conversation_id"],
                "created_at": row["created_at"],
                "updated_at": row["last_active_at"],
                "message_count": int(row["message_count"]),
                "modes": [item for item in str(row["modes"] or "").split(",") if item],
                "preview": str(row["latest_message"] or "")[:240],
            }
            for row in rows
        ]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.store._connection() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                return None
            messages = connection.execute(
                """SELECT message_id, sequence_number, role, mode, channel, content, created_at
                   FROM messages WHERE conversation_id = ? ORDER BY sequence_number""",
                (conversation_id,),
            ).fetchall()
        return {
            "conversation_id": conversation["conversation_id"],
            "title": conversation["title"],
            "state": conversation["state"],
            "parent_conversation_id": conversation["parent_conversation_id"],
            "created_at": conversation["created_at"],
            "updated_at": conversation["last_active_at"],
            "messages": [dict(row) for row in messages],
        }

    def list_domain_onboardings(self, *, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT d.*, w.title, w.state, w.created_at, w.updated_at,
                          COUNT(r.paper_id) AS recommendation_count
                   FROM domain_onboardings d
                   JOIN work_artifacts w ON w.artifact_id = d.artifact_id
                   LEFT JOIN domain_recommendations r ON r.artifact_id = d.artifact_id
                   WHERE w.title LIKE ? OR d.query LIKE ?
                   GROUP BY d.artifact_id ORDER BY w.updated_at DESC LIMIT ?""",
                (pattern, pattern, limit),
            ).fetchall()
        items = []
        for row in rows:
            quality = _loads(row["quality_json"], {})
            overview = _loads(row["overview_json"], {})
            items.append(
                {
                    "artifact_id": row["artifact_id"],
                    "title": row["title"],
                    "query": row["query"],
                    "language": row["language"],
                    "state": row["state"],
                    "current_stage": row["current_stage"],
                    "recommendation_count": int(row["recommendation_count"]),
                    "quality_score": quality.get("score") if isinstance(quality, dict) else None,
                    "preview": str(
                        overview.get("summary")
                        or overview.get("domain_definition")
                        or row["error_summary"]
                        or ""
                    )[:260],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return items

    def list_paper_readings(self, *, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT s.*, w.title, p.title AS paper_title, p.authors_json,
                          COUNT(DISTINCT a.annotation_id) AS annotation_count,
                          COUNT(DISTINCT b.reading_block_id) AS block_count
                   FROM paper_reading_sessions s
                   JOIN work_artifacts w ON w.artifact_id = s.artifact_id
                   JOIN papers p ON p.paper_id = s.paper_id
                   LEFT JOIN paper_annotations a ON a.reading_session_id = s.reading_session_id
                   LEFT JOIN paper_reading_blocks b ON b.reading_session_id = s.reading_session_id
                   WHERE w.title LIKE ? OR p.title LIKE ?
                   GROUP BY s.reading_session_id ORDER BY s.updated_at DESC LIMIT ?""",
                (pattern, pattern, limit),
            ).fetchall()
        return [
            {
                "reading_session_id": row["reading_session_id"],
                "paper_id": row["paper_id"],
                "conversation_id": row["conversation_id"],
                "parent_reading_session_id": row["parent_reading_session_id"],
                "title": row["title"],
                "paper_title": row["paper_title"],
                "authors": _loads(row["authors_json"], []),
                "state": row["state"],
                "current_section_id": row["current_section_id"],
                "progress": _loads(row["progress_json"], {}),
                "annotation_count": int(row["annotation_count"]),
                "block_count": int(row["block_count"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_papers(
        self, *, search: str = "", library_only: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        library_clause = "AND l.paper_id IS NOT NULL" if library_only else ""
        with self.store._connection() as connection:
            rows = connection.execute(
                f"""SELECT p.*, l.reading_status, l.note AS library_note, l.added_at,
                           COUNT(DISTINCT s.reading_session_id) AS reading_count,
                           COUNT(DISTINCT a.annotation_id) AS annotation_count,
                           CASE WHEN d.paper_id IS NULL THEN 0 ELSE 1 END AS has_document
                    FROM papers p
                    LEFT JOIN library_items l ON l.paper_id = p.paper_id
                    LEFT JOIN paper_documents d ON d.paper_id = p.paper_id
                    LEFT JOIN paper_reading_sessions s ON s.paper_id = p.paper_id
                    LEFT JOIN paper_annotations a ON a.paper_id = p.paper_id
                    WHERE (p.title LIKE ? OR p.abstract LIKE ? OR p.authors_json LIKE ?)
                    {library_clause}
                    GROUP BY p.paper_id ORDER BY COALESCE(l.updated_at, p.updated_at) DESC LIMIT ?""",
                (pattern, pattern, pattern, limit),
            ).fetchall()
        return [
            {
                "paper_id": row["paper_id"],
                "title": row["title"],
                "authors": _loads(row["authors_json"], []),
                "abstract": row["abstract"] or "",
                "publication_year": row["publication_year"],
                "venue": row["venue"] or "",
                "doi": row["doi"] or "",
                "arxiv_id": row["arxiv_id"] or "",
                "source_url": row["source_url"] or "",
                "in_library": row["reading_status"] is not None,
                "reading_status": row["reading_status"] or "",
                "library_note": row["library_note"] or "",
                "reading_count": int(row["reading_count"]),
                "annotation_count": int(row["annotation_count"]),
                "has_document": bool(row["has_document"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def set_library_item(self, paper_id: str, *, reading_status: str, note: str) -> bool:
        with self.store._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
        if exists is None:
            return False
        self.store.add_to_library(paper_id, reading_status=reading_status, note=note)
        return True

    def remove_library_item(self, paper_id: str) -> bool:
        with self.store._connection() as connection:
            cursor = connection.execute("DELETE FROM library_items WHERE paper_id = ?", (paper_id,))
        return cursor.rowcount > 0

    def list_annotations(
        self, paper_id: str, *, reading_session_id: str | None = None
    ) -> list[dict[str, Any]]:
        # Annotations belong to the paper; reading_session_id only records their origin.
        # Showing them across forks/sessions prevents one paper from having fragmented notes.
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM paper_annotations WHERE paper_id = ?
                   ORDER BY page_number, created_at""",
                (paper_id,),
            ).fetchall()
        return [self._annotation(row) for row in rows]

    def upsert_annotation(
        self,
        *,
        annotation_id: str,
        paper_id: str,
        reading_session_id: str | None,
        annotation_type: str,
        color: str,
        page_number: int,
        section_id: str | None,
        selected_text: str,
        rects: list[dict[str, float]],
        note_text: str,
    ) -> dict[str, Any] | None:
        now = _now()
        with self.store._connection() as connection:
            paper = connection.execute("SELECT 1 FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
            if paper is None:
                return None
            if reading_session_id:
                session = connection.execute(
                    """SELECT 1 FROM paper_reading_sessions
                       WHERE reading_session_id = ? AND paper_id = ?""",
                    (reading_session_id, paper_id),
                ).fetchone()
                if session is None:
                    reading_session_id = None
            connection.execute(
                """INSERT INTO paper_annotations(
                   annotation_id, paper_id, reading_session_id, annotation_type, color,
                   page_number, section_id, selected_text, anchor_schema_version,
                   anchor_json, note_text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pdf-rects-v1', ?, ?, ?, ?)
                   ON CONFLICT(annotation_id) DO UPDATE SET
                   reading_session_id = excluded.reading_session_id,
                   annotation_type = excluded.annotation_type, color = excluded.color,
                   page_number = excluded.page_number, section_id = excluded.section_id,
                   selected_text = excluded.selected_text, anchor_schema_version = excluded.anchor_schema_version,
                   anchor_json = excluded.anchor_json, note_text = excluded.note_text,
                   updated_at = excluded.updated_at
                   WHERE paper_annotations.paper_id = excluded.paper_id""",
                (
                    annotation_id, paper_id, reading_session_id, annotation_type, color,
                    page_number, section_id, selected_text, _json({"rects": rects}),
                    note_text, now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM paper_annotations WHERE annotation_id = ? AND paper_id = ?",
                (annotation_id, paper_id),
            ).fetchone()
        return self._annotation(row) if row else None

    def delete_annotation(self, paper_id: str, annotation_id: str) -> bool:
        with self.store._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM paper_annotations WHERE paper_id = ? AND annotation_id = ?",
                (paper_id, annotation_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _annotation(row: Any) -> dict[str, Any]:
        anchor = _loads(row["anchor_json"], {})
        return {
            "annotation_id": row["annotation_id"],
            "paper_id": row["paper_id"],
            "reading_session_id": row["reading_session_id"],
            "annotation_type": row["annotation_type"],
            "color": row["color"],
            "page_number": row["page_number"],
            "section_id": row["section_id"] or "",
            "selected_text": row["selected_text"],
            "rects": anchor.get("rects", []) if isinstance(anchor, dict) else [],
            "note_text": row["note_text"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
