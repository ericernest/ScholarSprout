"""Read models and paper-library operations for the local research center."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from .local_store import LocalResearchStore, _id, _json, _now


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _paper_authors(value: str | None) -> list[str]:
    authors = _loads(value, [])
    result: list[str] = []
    for author in authors if isinstance(authors, list) else []:
        candidate: Any = author
        if isinstance(candidate, str) and candidate.lstrip().startswith("{"):
            try:
                candidate = ast.literal_eval(candidate)
            except (ValueError, SyntaxError):
                pass
        if isinstance(candidate, dict):
            candidate = candidate.get("name") or ""
        name = re.sub(r"\s+", " ", str(candidate or "")).strip()
        if name and not name.startswith("{") and name not in result:
            result.append(name)
    return result


def _conversation_display_title(value: Any) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"^论文精读\s*[:：]\s*", "", title, count=1).strip() or "新会话"


def _paper_display_fields(
    title_value: Any, abstract_value: Any, document_value: Any = None
) -> tuple[str, str]:
    title = str(title_value or "").strip()
    abstract = str(abstract_value or "").strip()
    for raw, target in ((title, "title"), (abstract, "abstract")):
        if not raw.lstrip().startswith(("{", "[")):
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            try:
                parsed = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            continue
        if target == "title":
            title = str(parsed.get("title") or parsed.get("paper_title") or parsed.get("name") or "").strip()
            abstract = abstract or str(parsed.get("abstract") or parsed.get("summary") or "").strip()
        else:
            abstract = str(parsed.get("abstract") or parsed.get("summary") or parsed.get("text") or "").strip()
    title = re.sub(r"\s+", " ", title).strip(" \t\r\n\"'")
    abstract = re.sub(r"\s+", " ", abstract).strip(" \t\r\n\"'")
    title = re.sub(
        r"^Published\s+in\s+.+?\(\d{1,2}/\d{4}\)\s*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    document = _loads(document_value, {}) if isinstance(document_value, str) else document_value
    if _looks_like_publication_header(title) and isinstance(document, dict):
        repaired = _title_from_document_text(str(document.get("full_text") or ""))
        if repaired:
            title = repaired
    parts = re.split(r"\s+(?:Abstract|摘要)\s*[:：.\-—–]?\s*", title, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2 and len(parts[1]) >= 40:
        title = parts[0].strip()
        abstract = abstract or parts[1].strip()
    author_suffix = re.search(
        r"\s+(?=[A-Z][A-Za-z'’\-]+(?:\s+(?:and|[A-Z][A-Za-z'’\-]+)){1,6}\d[∗†‡*]?(?:\s|$))",
        title,
    )
    if author_suffix and author_suffix.start() >= 12:
        title = title[:author_suffix.start()].strip()
    if len(title) > 240:
        sentence = re.split(r"(?<=[.!?。！？])\s+", title, maxsplit=1)[0]
        title = sentence if len(sentence) <= 240 else f"{title[:237].rstrip()}…"
    if title.startswith(("{", "[")) or not title:
        title = "未命名论文"
    return title, abstract


def _looks_like_publication_header(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value or "").strip().lower()
    return bool(
        re.match(r"^(?:ieee|acm)\s+transactions?.*\bvol\.?\s+", normalized)
        or normalized.startswith("published in ")
    )


def _title_from_document_text(text: str) -> str:
    candidates: list[str] = []
    for raw_line in text.splitlines()[:24]:
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line.lower() in {"abstract", "摘要"}:
            if candidates:
                break
            continue
        line = re.sub(
            r"^Published\s+in\s+.+?\(\d{1,2}/\d{4}\)\s*",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        if not line or _looks_like_publication_header(line) or re.fullmatch(r"\d+", line):
            continue
        if line.startswith(("arXiv", "http", "DOI")):
            continue
        if candidates and (
            "," in line
            or "@" in line
            or re.search(r"\b(?:University|Institute|Laboratory|Corresponding author)\b", line)
        ):
            break
        if 5 < len(line) < 220:
            candidates.append(line)
        if len(" ".join(candidates)) >= 20 and line.endswith((".", "?", "!")):
            break
    return " ".join(candidates[:3]).strip()[:300]


class ResearchCatalog:
    """Stable list/detail queries kept separate from pipeline write methods."""

    def __init__(self, store: LocalResearchStore) -> None:
        self.store = store

    def counts(self, *, user_id: str | None = None) -> dict[str, int]:
        with self.store._connection() as connection:
            row = connection.execute(
                """SELECT
                   (SELECT COUNT(*) FROM conversations c
                    WHERE (? IS NULL OR c.user_id = ?)
                      AND c.parent_conversation_id IS NULL
                      AND EXISTS (SELECT 1 FROM messages visible_message
                                  WHERE visible_message.conversation_id = c.conversation_id)
                      AND NOT EXISTS (SELECT 1 FROM paper_reading_sessions hidden
                                      WHERE hidden.dialogue_conversation_id = c.conversation_id)
                      AND NOT (c.conversation_id LIKE 'paper-reading-%'
                               AND NOT EXISTS (SELECT 1 FROM paper_reading_sessions visible
                                               WHERE visible.conversation_id = c.conversation_id))) AS conversations,
                   (SELECT COUNT(*) FROM (
                      SELECT LOWER(TRIM(REPLACE(REPLACE(REPLACE(d.query, '  ', ' '), '  ', ' '), '  ', ' '))) AS normalized_query,
                             COALESCE((SELECT link.conversation_id FROM conversation_artifacts link
                                       WHERE link.artifact_id = d.artifact_id
                                       ORDER BY link.linked_at DESC LIMIT 1), '') AS conversation_id
                      FROM domain_onboardings d
                      WHERE (? IS NULL OR COALESCE(
                          NULLIF(d.user_id, ''),
                          (SELECT owner_conversation.user_id
                           FROM conversation_artifacts owner_link
                           JOIN conversations owner_conversation
                             ON owner_conversation.conversation_id = owner_link.conversation_id
                           WHERE owner_link.artifact_id = d.artifact_id
                           ORDER BY CASE owner_link.relation WHEN 'created' THEN 0 ELSE 1 END,
                                    owner_link.linked_at ASC
                           LIMIT 1)
                      ) = ?)
                      GROUP BY normalized_query, conversation_id
                    )) AS domain_onboardings,
                   (SELECT COUNT(DISTINCT reading.paper_id)
                      FROM paper_reading_sessions reading
                      LEFT JOIN conversations owner_conversation
                        ON owner_conversation.conversation_id = reading.conversation_id
                     WHERE (? IS NULL OR COALESCE(NULLIF(reading.user_id, ''), owner_conversation.user_id) = ?)
                   ) AS paper_readings""",
                (user_id, user_id, user_id, user_id, user_id, user_id),
            ).fetchone()
            result = {key: int(row[key]) for key in row.keys()}
            if user_id is None:
                library = connection.execute(
                    """SELECT COUNT(*) AS library_papers,
                              SUM(CASE WHEN folder_id IS NULL THEN 1 ELSE 0 END) AS unfiled_papers
                       FROM library_items"""
                ).fetchone()
                result["library_papers"] = int(library["library_papers"] or 0)
                result["unfiled_papers"] = int(library["unfiled_papers"] or 0)
                result["all_papers"] = int(
                    connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
                )
            else:
                library = connection.execute(
                    """SELECT COUNT(*) AS library_papers,
                              SUM(CASE WHEN folder_id IS NULL THEN 1 ELSE 0 END) AS unfiled_papers
                       FROM user_library_items WHERE user_id = ?""",
                    (user_id,),
                ).fetchone()
                result["library_papers"] = int(library["library_papers"] or 0)
                result["unfiled_papers"] = int(library["unfiled_papers"] or 0)
                result["all_papers"] = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM (
                               SELECT paper_id FROM user_library_items WHERE user_id = ?
                               UNION
                               SELECT paper_id FROM paper_reading_access WHERE user_id = ?
                           )""",
                        (user_id, user_id),
                    ).fetchone()[0]
                )
        return result

    def list_conversations(
        self, *, search: str = "", limit: int = 100, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT c.conversation_id, c.title, c.state, c.parent_conversation_id,
                          c.created_at, c.last_active_at, COUNT(m.message_id) AS message_count,
                          GROUP_CONCAT(DISTINCT m.mode) AS modes,
                          (SELECT linked_artifact.artifact_kind
                           FROM conversation_artifacts link
                           JOIN work_artifacts linked_artifact ON linked_artifact.artifact_id = link.artifact_id
                           WHERE link.conversation_id = c.conversation_id
                           ORDER BY link.linked_at DESC LIMIT 1) AS workspace_kind,
                          (SELECT link.artifact_id
                           FROM conversation_artifacts link
                           WHERE link.conversation_id = c.conversation_id
                           ORDER BY link.linked_at DESC LIMIT 1) AS workspace_artifact_id,
                          (SELECT reading.reading_session_id
                           FROM paper_reading_sessions reading
                           WHERE reading.conversation_id = c.conversation_id
                           ORDER BY reading.updated_at DESC LIMIT 1) AS reading_session_id,
                          (SELECT reading.paper_id
                           FROM paper_reading_sessions reading
                           WHERE reading.conversation_id = c.conversation_id
                           ORDER BY reading.updated_at DESC LIMIT 1) AS paper_id,
                          (SELECT content FROM messages latest
                           WHERE latest.conversation_id = c.conversation_id
                           ORDER BY latest.sequence_number DESC LIMIT 1) AS latest_message
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.conversation_id
                    WHERE (? IS NULL OR c.user_id = ?)
                    AND c.parent_conversation_id IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM paper_reading_sessions hidden
                        WHERE hidden.dialogue_conversation_id = c.conversation_id
                    ) AND NOT (
                       c.conversation_id LIKE 'paper-reading-%'
                       AND NOT EXISTS (
                           SELECT 1 FROM paper_reading_sessions prs
                           WHERE prs.conversation_id = c.conversation_id
                       )
                   ) AND (c.title LIKE ? OR EXISTS (
                       SELECT 1 FROM messages found
                       WHERE found.conversation_id = c.conversation_id AND found.content LIKE ?))
                   GROUP BY c.conversation_id
                   HAVING COUNT(m.message_id) > 0
                   ORDER BY c.last_active_at DESC LIMIT ?""",
                 (user_id, user_id, pattern, pattern, limit),
            ).fetchall()
        return [
            {
                "conversation_id": row["conversation_id"],
                "title": _conversation_display_title(row["title"]),
                "state": row["state"],
                "parent_conversation_id": row["parent_conversation_id"],
                "created_at": row["created_at"],
                "updated_at": row["last_active_at"],
                "message_count": int(row["message_count"]),
                "modes": [item for item in str(row["modes"] or "").split(",") if item],
                "workspace_kind": row["workspace_kind"] or "chat",
                "workspace_artifact_id": row["workspace_artifact_id"] or "",
                "reading_session_id": row["reading_session_id"] or "",
                "paper_id": row["paper_id"] or "",
                "preview": str(row["latest_message"] or "")[:240],
            }
            for row in rows
        ]

    def get_conversation(
        self, conversation_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.store._connection() as connection:
            conversation = connection.execute(
                """SELECT * FROM conversations
                   WHERE conversation_id = ? AND (? IS NULL OR user_id = ?)""",
                (conversation_id, user_id, user_id),
            ).fetchone()
            if conversation is None:
                return None
            messages = connection.execute(
                """SELECT message_id, sequence_number, role, mode, channel, content, created_at
                   FROM messages WHERE conversation_id = ? ORDER BY sequence_number""",
                (conversation_id,),
            ).fetchall()
            workspace = connection.execute(
                """SELECT artifact.artifact_kind, link.artifact_id
                   FROM conversation_artifacts link
                   JOIN work_artifacts artifact ON artifact.artifact_id = link.artifact_id
                   WHERE link.conversation_id = ?
                   ORDER BY link.linked_at DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone()
            reading = connection.execute(
                """SELECT reading_session_id, paper_id
                   FROM paper_reading_sessions
                   WHERE conversation_id = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone()
            context_rows = connection.execute(
                """SELECT artifact.artifact_kind, artifact.artifact_id, artifact.title,
                          link.relation, link.linked_at, reading.reading_session_id, reading.paper_id,
                          paper.title AS paper_title, domain.query AS domain_query
                   FROM conversation_artifacts link
                   JOIN work_artifacts artifact ON artifact.artifact_id = link.artifact_id
                   LEFT JOIN paper_reading_sessions reading ON reading.artifact_id = artifact.artifact_id
                   LEFT JOIN papers paper ON paper.paper_id = reading.paper_id
                   LEFT JOIN domain_onboardings domain ON domain.artifact_id = artifact.artifact_id
                   WHERE link.conversation_id = ?
                   ORDER BY link.linked_at DESC""",
                (conversation_id,),
            ).fetchall()
        contexts = []
        seen_contexts: set[tuple[str, str]] = set()
        for row in context_rows:
            kind = str(row["artifact_kind"] or "")
            context_id = (
                str(row["reading_session_id"] or "")
                if kind == "paper_reading"
                else str(row["artifact_id"])
            )
            title = str(row["paper_title"] or row["domain_query"] or row["title"] or "")
            identity = (
                kind,
                str(row["paper_id"] or context_id)
                if kind == "paper_reading"
                else context_id,
            )
            if (
                context_id
                and kind in {"paper_reading", "domain_onboarding"}
                and identity not in seen_contexts
            ):
                seen_contexts.add(identity)
                contexts.append({
                    "kind": kind,
                    "id": context_id,
                    "artifact_id": str(row["artifact_id"]),
                    "paper_id": str(row["paper_id"] or ""),
                    "title": title,
                    "relation": str(row["relation"] or "created"),
                    "linked_at": str(row["linked_at"]),
                })
        contexts.reverse()
        return {
            "conversation_id": conversation["conversation_id"],
            "title": _conversation_display_title(conversation["title"]),
            "state": conversation["state"],
            "parent_conversation_id": conversation["parent_conversation_id"],
            "created_at": conversation["created_at"],
            "updated_at": conversation["last_active_at"],
            "workspace_kind": workspace["artifact_kind"] if workspace else "chat",
            "workspace_artifact_id": workspace["artifact_id"] if workspace else "",
            "reading_session_id": reading["reading_session_id"] if reading else "",
            "paper_id": reading["paper_id"] if reading else "",
            "contexts": contexts,
            "messages": [dict(row) for row in messages],
        }

    def list_context_candidates(
        self,
        *,
        conversation_id: str,
        search: str = "",
        limit: int = 100,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List persisted research workspaces that can be brought into a chat."""
        if user_id is not None and self.get_conversation(conversation_id, user_id=user_id) is None:
            return []
        pattern = f"%{search.strip()}%"
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT w.artifact_id, w.artifact_kind, w.title, w.state, w.updated_at,
                          r.reading_session_id, r.paper_id, p.title AS paper_title,
                          d.query AS domain_query,
                          CASE WHEN link.artifact_id IS NULL THEN 0 ELSE 1 END AS linked
                   FROM work_artifacts w
                   LEFT JOIN paper_reading_sessions r ON r.artifact_id = w.artifact_id
                   LEFT JOIN papers p ON p.paper_id = r.paper_id
                   LEFT JOIN domain_onboardings d ON d.artifact_id = w.artifact_id
                   LEFT JOIN conversation_artifacts link
                     ON link.conversation_id = ? AND link.artifact_id = w.artifact_id
                   WHERE w.artifact_kind IN ('paper_reading', 'domain_onboarding')
                     AND (w.title LIKE ? OR p.title LIKE ? OR d.query LIKE ?)
                     AND (
                         ? IS NULL
                         OR (
                             w.artifact_kind = 'paper_reading'
                             AND COALESCE(
                                 NULLIF(r.user_id, ''),
                                 (SELECT reading_conversation.user_id
                                  FROM conversations reading_conversation
                                  WHERE reading_conversation.conversation_id = r.conversation_id)
                             ) = ?
                         )
                         OR (
                             w.artifact_kind = 'domain_onboarding'
                             AND COALESCE(
                                 NULLIF(d.user_id, ''),
                                 (SELECT owned_conversation.user_id
                                  FROM conversation_artifacts owned_link
                                  JOIN conversations owned_conversation
                                    ON owned_conversation.conversation_id = owned_link.conversation_id
                                  WHERE owned_link.artifact_id = w.artifact_id
                                  ORDER BY CASE owned_link.relation WHEN 'created' THEN 0 ELSE 1 END,
                                           owned_link.linked_at ASC
                                  LIMIT 1)
                             ) = ?
                         )
                     )
                   ORDER BY linked DESC, w.updated_at DESC LIMIT ?""",
                (
                    conversation_id,
                    pattern,
                    pattern,
                    pattern,
                    user_id,
                    user_id,
                    user_id,
                    limit,
                ),
            ).fetchall()
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            kind = str(row["artifact_kind"] or "")
            context_id = (
                str(row["reading_session_id"] or "")
                if kind == "paper_reading"
                else str(row["artifact_id"])
            )
            identity = (kind, str(row["paper_id"] or context_id) if kind == "paper_reading" else context_id)
            if not context_id or identity in seen:
                continue
            seen.add(identity)
            items.append({
                "kind": kind,
                "id": context_id,
                "artifact_id": str(row["artifact_id"]),
                "paper_id": str(row["paper_id"] or ""),
                "title": str(row["paper_title"] or row["domain_query"] or row["title"] or ""),
                "state": str(row["state"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "linked": bool(row["linked"]),
            })
        return items

    def list_domain_onboardings(
        self, *, search: str = "", limit: int = 100, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT d.*, w.title, w.state, w.created_at, w.updated_at,
                          (SELECT link.conversation_id FROM conversation_artifacts link
                           WHERE link.artifact_id = d.artifact_id
                           ORDER BY link.linked_at DESC LIMIT 1) AS conversation_id,
                          COUNT(r.paper_id) AS recommendation_count
                   FROM domain_onboardings d
                   JOIN work_artifacts w ON w.artifact_id = d.artifact_id
                   LEFT JOIN domain_recommendations r ON r.artifact_id = d.artifact_id
                   WHERE (w.title LIKE ? OR d.query LIKE ?)
                     AND (? IS NULL OR COALESCE(
                         NULLIF(d.user_id, ''),
                         (SELECT owner_conversation.user_id
                          FROM conversation_artifacts owner_link
                          JOIN conversations owner_conversation
                            ON owner_conversation.conversation_id = owner_link.conversation_id
                          WHERE owner_link.artifact_id = d.artifact_id
                          ORDER BY CASE owner_link.relation WHEN 'created' THEN 0 ELSE 1 END,
                                   owner_link.linked_at ASC
                          LIMIT 1)
                     ) = ?)
                   GROUP BY d.artifact_id ORDER BY w.updated_at DESC LIMIT ?""",
                (pattern, pattern, user_id, user_id, max(limit * 4, limit)),
            ).fetchall()
            job_results: dict[str, dict[str, Any]] = {}
            has_jobs = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone()
            if rows and has_jobs:
                artifact_ids = [str(row["artifact_id"]) for row in rows]
                placeholders = ",".join("?" for _ in artifact_ids)
                job_rows = connection.execute(
                    f"SELECT task_id, result_json FROM jobs WHERE task_id IN ({placeholders})",
                    artifact_ids,
                ).fetchall()
                job_results = {
                    str(row["task_id"]): _loads(row["result_json"], {})
                    for row in job_rows
                }
        items = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            identity = (
                str(row["conversation_id"] or ""),
                " ".join(str(row["query"] or "").casefold().split()),
            )
            if identity in seen:
                continue
            seen.add(identity)
            overview = _loads(row["overview_json"], {})
            job_result = job_results.get(str(row["artifact_id"]), {})
            job_papers = job_result.get("papers")
            job_recommendation_count = (
                len(job_papers) if isinstance(job_papers, list) else None
            )
            persisted_count = int(row["recommendation_count"])
            items.append(
                {
                    "artifact_id": row["artifact_id"],
                    "conversation_id": row["conversation_id"] or "",
                    "title": row["title"],
                    "query": row["query"],
                    "language": row["language"],
                    "state": row["state"],
                    "current_stage": row["current_stage"],
                    "recommendation_count": (
                        job_recommendation_count
                        if job_recommendation_count is not None
                        else persisted_count
                    ),
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
        return items[:limit]

    def get_domain_onboarding(
        self, artifact_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.store._connection() as connection:
            row = connection.execute(
                """SELECT d.*, w.title, w.state, w.created_at, w.updated_at,
                          (SELECT link.conversation_id FROM conversation_artifacts link
                           WHERE link.artifact_id = d.artifact_id
                           ORDER BY link.linked_at DESC LIMIT 1) AS conversation_id
                   FROM domain_onboardings d JOIN work_artifacts w USING(artifact_id)
                   WHERE d.artifact_id = ?
                     AND (? IS NULL OR COALESCE(
                         NULLIF(d.user_id, ''),
                         (SELECT owner_conversation.user_id
                          FROM conversation_artifacts owner_link
                          JOIN conversations owner_conversation
                            ON owner_conversation.conversation_id = owner_link.conversation_id
                          WHERE owner_link.artifact_id = d.artifact_id
                          ORDER BY CASE owner_link.relation WHEN 'created' THEN 0 ELSE 1 END,
                                   owner_link.linked_at ASC
                          LIMIT 1)
                     ) = ?)""",
                (artifact_id, user_id, user_id),
            ).fetchone()
            if row is None:
                return None
            recommendations = connection.execute(
                """SELECT r.*, p.title, p.authors_json, p.abstract, p.publication_year,
                          p.venue, p.doi, p.arxiv_id, p.source_url,
                          CASE WHEN l.paper_id IS NULL THEN 0 ELSE 1 END AS in_library
                   FROM domain_recommendations r
                   JOIN papers p ON p.paper_id = r.paper_id
                   LEFT JOIN library_items l ON l.paper_id = p.paper_id
                   WHERE r.artifact_id = ? ORDER BY r.recommendation_rank""",
                (artifact_id,),
            ).fetchall()
        recommendation_items = []
        for item in recommendations:
            display_title, display_abstract = _paper_display_fields(
                item["title"], item["abstract"]
            )
            recommendation_items.append(
                {
                    "paper_id": item["paper_id"],
                    "title": display_title,
                    "authors": _paper_authors(item["authors_json"]),
                    "abstract": display_abstract,
                    "publication_year": item["publication_year"],
                    "venue": item["venue"] or "",
                    "doi": item["doi"] or "",
                    "arxiv_id": item["arxiv_id"] or "",
                    "source_url": item["source_url"] or "",
                    "reason": item["reason"],
                    "reading_focus": _loads(item["reading_focus_json"], []),
                    "reading_priority": item["reading_priority"],
                    "paper_role": item["paper_role"],
                    "is_canonical": bool(item["is_canonical"]),
                    "in_library": bool(item["in_library"]),
                }
            )
        return {
            "artifact_id": row["artifact_id"],
            "conversation_id": row["conversation_id"] or "",
            "title": row["title"],
            "query": row["query"],
            "state": row["state"],
            "current_stage": row["current_stage"],
            "output_schema_version": row["output_schema_version"] or "",
            "learner_profile": _loads(row["learner_profile_json"], {}),
            "overview": _loads(row["overview_json"], {}),
            "research_plan": _loads(row["research_plan_json"], {}),
            "learning_path": _loads(row["learning_path_json"], []),
            "knowledge_graph": _loads(row["knowledge_graph_json"], {}),
            "error_summary": row["error_summary"] or "",
            "recommendations": recommendation_items,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_paper_readings(
        self, *, search: str = "", limit: int = 100, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        annotation_join = (
            "LEFT JOIN paper_annotations a ON a.reading_session_id = s.reading_session_id"
            if user_id is None
            else "LEFT JOIN user_paper_annotations a ON a.reading_session_id = s.reading_session_id AND a.user_id = ?"
        )
        parameters: list[Any] = []
        if user_id is not None:
            parameters.append(user_id)
        parameters.extend([pattern, pattern, user_id, user_id, max(limit * 4, limit)])
        with self.store._connection() as connection:
            rows = connection.execute(
                f"""SELECT s.*, w.title, p.title AS paper_title, p.abstract AS paper_abstract,
                          p.authors_json,
                          p.publication_year, p.venue, d.document_json,
                          COUNT(DISTINCT a.annotation_id) AS annotation_count,
                          COUNT(DISTINCT b.reading_block_id) AS block_count
                   FROM paper_reading_sessions s
                   JOIN work_artifacts w ON w.artifact_id = s.artifact_id
                   JOIN papers p ON p.paper_id = s.paper_id
                   LEFT JOIN conversations owner_conversation
                     ON owner_conversation.conversation_id = s.conversation_id
                   LEFT JOIN paper_documents d ON d.paper_id = p.paper_id
                   {annotation_join}
                   LEFT JOIN paper_reading_blocks b ON b.reading_session_id = s.reading_session_id
                   WHERE (w.title LIKE ? OR p.title LIKE ?)
                     AND (? IS NULL OR COALESCE(NULLIF(s.user_id, ''), owner_conversation.user_id) = ?)
                   GROUP BY s.reading_session_id ORDER BY s.updated_at DESC LIMIT ?""",
                 parameters,
            ).fetchall()
        items = []
        seen_papers: set[str] = set()
        for row in rows:
            paper_identity = str(row["paper_id"] or "")
            if paper_identity in seen_papers:
                continue
            seen_papers.add(paper_identity)
            document = _loads(row["document_json"], {})
            display_title, display_abstract = _paper_display_fields(
                row["paper_title"], row["paper_abstract"], row["document_json"]
            )
            current_section_title = ""
            for section in document.get("sections", []) if isinstance(document, dict) else []:
                if str(section.get("section_id") or "") == str(row["current_section_id"] or ""):
                    current_section_title = str(section.get("title") or "")
                    break
            items.append({
                "reading_session_id": row["reading_session_id"],
                "paper_id": row["paper_id"],
                "conversation_id": row["conversation_id"],
                "parent_reading_session_id": row["parent_reading_session_id"],
                "title": row["title"],
                "paper_title": display_title,
                "paper_abstract": display_abstract,
                "authors": _paper_authors(row["authors_json"]),
                "publication_year": row["publication_year"],
                "venue": row["venue"] or "",
                "state": row["state"],
                "current_section_id": row["current_section_id"],
                "current_section_title": current_section_title,
                "progress": _loads(row["progress_json"], {}),
                "annotation_count": int(row["annotation_count"]),
                "block_count": int(row["block_count"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return items[:limit]

    def list_papers(
        self,
        *,
        search: str = "",
        library_only: bool = False,
        folder_id: str | None = None,
        reading_scope: str = "all",
        limit: int = 100,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if user_id is not None:
            return self._list_papers_scoped(
                search=search,
                library_only=library_only,
                folder_id=folder_id,
                reading_scope=reading_scope,
                limit=limit,
                user_id=user_id,
            )
        pattern = f"%{search.strip()}%"
        library_clause = "AND l.paper_id IS NOT NULL" if library_only else ""
        if folder_id == "__unfiled__":
            folder_clause = "AND l.folder_id IS NULL"
        elif folder_id:
            folder_clause = """AND l.folder_id IN (
                WITH RECURSIVE subtree(folder_id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT child.folder_id FROM paper_folders child
                    JOIN subtree parent ON child.parent_folder_id = parent.folder_id
                )
                SELECT folder_id FROM subtree
            )"""
        else:
            folder_clause = ""
        if reading_scope == "reviewed":
            reading_clause = """AND EXISTS (
                SELECT 1 FROM paper_reading_sessions reviewed
                WHERE reviewed.paper_id = p.paper_id
            )"""
        elif reading_scope == "unreviewed":
            reading_clause = """AND NOT EXISTS (
                SELECT 1 FROM paper_reading_sessions reviewed
                WHERE reviewed.paper_id = p.paper_id
            )"""
        else:
            reading_clause = ""
        parameters: list[Any] = [pattern, pattern, pattern]
        if folder_id and folder_id != "__unfiled__":
            parameters.append(folder_id)
        parameters.append(limit)
        with self.store._connection() as connection:
            rows = connection.execute(
                f"""SELECT p.*, l.reading_status, l.note AS library_note, l.added_at,
                           l.folder_id, f.name AS folder_name,
                           COUNT(DISTINCT s.reading_session_id) AS reading_count,
                           (SELECT latest.reading_session_id
                            FROM paper_reading_sessions latest
                            WHERE latest.paper_id = p.paper_id
                            ORDER BY latest.updated_at DESC LIMIT 1) AS latest_reading_session_id,
                           COUNT(DISTINCT a.annotation_id) AS annotation_count,
                           CASE WHEN d.paper_id IS NULL THEN 0 ELSE 1 END AS has_document,
                           d.document_json AS document_json
                    FROM papers p
                    LEFT JOIN library_items l ON l.paper_id = p.paper_id
                    LEFT JOIN paper_folders f ON f.folder_id = l.folder_id
                    LEFT JOIN paper_documents d ON d.paper_id = p.paper_id
                    LEFT JOIN paper_reading_sessions s ON s.paper_id = p.paper_id
                    LEFT JOIN paper_annotations a ON a.paper_id = p.paper_id
                    WHERE (p.title LIKE ? OR p.abstract LIKE ? OR p.authors_json LIKE ?)
                    {library_clause}
                    {folder_clause}
                    {reading_clause}
                    GROUP BY p.paper_id ORDER BY COALESCE(l.updated_at, p.updated_at) DESC LIMIT ?""",
                parameters,
            ).fetchall()
        folder_paths = {
            folder["folder_id"]: folder["path"] for folder in self.list_folders()
        }
        items = []
        for row in rows:
            display_title, display_abstract = _paper_display_fields(
                row["title"], row["abstract"], row["document_json"]
            )
            items.append({
                "paper_id": row["paper_id"],
                "title": display_title,
                "authors": _paper_authors(row["authors_json"]),
                "abstract": display_abstract,
                "publication_year": row["publication_year"],
                "venue": row["venue"] or "",
                "doi": row["doi"] or "",
                "arxiv_id": row["arxiv_id"] or "",
                "source_url": row["source_url"] or "",
                "in_library": row["reading_status"] is not None,
                "reading_status": row["reading_status"] or "",
                "library_note": row["library_note"] or "",
                "folder_id": row["folder_id"] or "",
                "folder_name": row["folder_name"] or "",
                "folder_path": folder_paths.get(row["folder_id"], ""),
                "reading_count": int(row["reading_count"]),
                "latest_reading_session_id": row["latest_reading_session_id"] or "",
                "annotation_count": int(row["annotation_count"]),
                "has_document": bool(row["has_document"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return items

    def set_library_item(
        self,
        paper_id: str,
        *,
        reading_status: str,
        note: str,
        folder_id: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        if user_id is not None:
            return self._set_library_item_scoped(
                paper_id,
                reading_status=reading_status,
                note=note,
                folder_id=folder_id,
                user_id=user_id,
            )
        with self.store._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if folder_id and connection.execute(
                "SELECT 1 FROM paper_folders WHERE folder_id = ?", (folder_id,)
            ).fetchone() is None:
                return False
        if exists is None:
            return False
        self.store.add_to_library(
            paper_id, reading_status=reading_status, note=note, folder_id=folder_id
        )
        return True

    def get_paper_note(
        self, paper_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        if user_id is not None:
            return self._get_paper_note_scoped(paper_id, user_id=user_id)
        with self.store._connection() as connection:
            paper = connection.execute(
                "SELECT title, abstract FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                return None
            note = connection.execute(
                "SELECT content_markdown, created_at, updated_at FROM paper_notes WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
        display_title, _ = _paper_display_fields(paper["title"], paper["abstract"])
        return {
            "paper_id": paper_id,
            "paper_title": display_title,
            "format": "markdown",
            "content_markdown": note["content_markdown"] if note else "",
            "created_at": note["created_at"] if note else None,
            "updated_at": note["updated_at"] if note else None,
        }

    def set_paper_note(
        self, paper_id: str, content_markdown: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        if user_id is not None:
            return self._set_paper_note_scoped(
                paper_id, content_markdown, user_id=user_id
            )
        now = _now()
        with self.store._connection() as connection:
            paper = connection.execute(
                "SELECT title FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                return None
            connection.execute(
                """INSERT INTO paper_notes(paper_id, content_markdown, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(paper_id) DO UPDATE SET
                   content_markdown = excluded.content_markdown,
                   updated_at = excluded.updated_at""",
                (paper_id, content_markdown, now, now),
            )
        return self.get_paper_note(paper_id)

    def list_folders(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        if user_id is not None:
            return self._list_folders_scoped(user_id=user_id)
        with self.store._connection() as connection:
            rows = connection.execute(
                """WITH RECURSIVE folder_tree AS (
                       SELECT folder_id, name, parent_folder_id, created_at, updated_at,
                              name AS path, 0 AS depth
                       FROM paper_folders WHERE parent_folder_id IS NULL
                       UNION ALL
                       SELECT child.folder_id, child.name, child.parent_folder_id,
                              child.created_at, child.updated_at,
                              tree.path || ' / ' || child.name, tree.depth + 1
                       FROM paper_folders child
                       JOIN folder_tree tree ON child.parent_folder_id = tree.folder_id
                   )
                   SELECT tree.*, COUNT(l.paper_id) AS paper_count
                   FROM folder_tree tree
                   LEFT JOIN library_items l ON l.folder_id = tree.folder_id
                   GROUP BY tree.folder_id
                   ORDER BY tree.path COLLATE NOCASE"""
            ).fetchall()
        return [dict(row) for row in rows]

    def create_folder(
        self,
        name: str,
        *,
        parent_folder_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if user_id is not None:
            return self._create_folder_scoped(
                name, parent_folder_id=parent_folder_id, user_id=user_id
            )
        now = _now()
        folder_id = _id("folder")
        with self.store._connection() as connection:
            if parent_folder_id and connection.execute(
                "SELECT 1 FROM paper_folders WHERE folder_id = ?", (parent_folder_id,)
            ).fetchone() is None:
                raise ValueError("上级文件夹不存在。")
            connection.execute(
                """INSERT INTO paper_folders(folder_id, name, parent_folder_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (folder_id, name.strip(), parent_folder_id, now, now),
            )
        return {
            "folder_id": folder_id,
            "name": name.strip(),
            "parent_folder_id": parent_folder_id,
            "paper_count": 0,
        }

    def update_folder(
        self,
        folder_id: str,
        *,
        name: str,
        parent_folder_id: str | None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        if user_id is not None:
            return self._update_folder_scoped(
                folder_id,
                name=name,
                parent_folder_id=parent_folder_id,
                user_id=user_id,
            )
        with self.store._connection() as connection:
            current = connection.execute(
                "SELECT * FROM paper_folders WHERE folder_id = ?", (folder_id,)
            ).fetchone()
            if current is None:
                return None
            if parent_folder_id == folder_id:
                raise ValueError("文件夹不能移动到自身。")
            if parent_folder_id:
                parent = connection.execute(
                    "SELECT 1 FROM paper_folders WHERE folder_id = ?", (parent_folder_id,)
                ).fetchone()
                if parent is None:
                    raise ValueError("目标文件夹不存在。")
                descendant = connection.execute(
                    """WITH RECURSIVE descendants(folder_id) AS (
                           SELECT folder_id FROM paper_folders WHERE parent_folder_id = ?
                           UNION ALL
                           SELECT child.folder_id FROM paper_folders child
                           JOIN descendants parent ON child.parent_folder_id = parent.folder_id
                       )
                       SELECT 1 FROM descendants WHERE folder_id = ?""",
                    (folder_id, parent_folder_id),
                ).fetchone()
                if descendant is not None:
                    raise ValueError("文件夹不能移动到自己的子文件夹中。")
            now = _now()
            connection.execute(
                """UPDATE paper_folders SET name = ?, parent_folder_id = ?, updated_at = ?
                   WHERE folder_id = ?""",
                (name.strip(), parent_folder_id, now, folder_id),
            )
        return {
            "folder_id": folder_id,
            "name": name.strip(),
            "parent_folder_id": parent_folder_id,
        }

    def delete_folder(self, folder_id: str, *, user_id: str | None = None) -> bool:
        if user_id is not None:
            return self._delete_folder_scoped(folder_id, user_id=user_id)
        with self.store._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM paper_folders WHERE folder_id = ?", (folder_id,)
            ).fetchone()
            if exists is None:
                return False
            has_contents = connection.execute(
                """SELECT EXISTS(SELECT 1 FROM paper_folders WHERE parent_folder_id = ?)
                          OR EXISTS(SELECT 1 FROM library_items WHERE folder_id = ?) AS value""",
                (folder_id, folder_id),
            ).fetchone()
            if bool(has_contents["value"]):
                raise ValueError("文件夹不为空，请先移动其中的论文和子文件夹。")
            cursor = connection.execute("DELETE FROM paper_folders WHERE folder_id = ?", (folder_id,))
        return cursor.rowcount > 0

    def remove_library_item(
        self, paper_id: str, *, user_id: str | None = None
    ) -> bool:
        if user_id is not None:
            with self.store._connection() as connection:
                cursor = connection.execute(
                    "DELETE FROM user_library_items WHERE user_id = ? AND paper_id = ?",
                    (user_id, paper_id),
                )
            return cursor.rowcount > 0
        with self.store._connection() as connection:
            cursor = connection.execute("DELETE FROM library_items WHERE paper_id = ?", (paper_id,))
        return cursor.rowcount > 0

    def move_library_item(
        self,
        paper_id: str,
        *,
        folder_id: str | None,
        user_id: str | None = None,
    ) -> bool:
        if user_id is not None:
            return self._move_library_item_scoped(
                paper_id, folder_id=folder_id, user_id=user_id
            )
        with self.store._connection() as connection:
            if folder_id and connection.execute(
                "SELECT 1 FROM paper_folders WHERE folder_id = ?", (folder_id,)
            ).fetchone() is None:
                raise ValueError("目标文件夹不存在。")
            cursor = connection.execute(
                "UPDATE library_items SET folder_id = ?, updated_at = ? WHERE paper_id = ?",
                (folder_id, _now(), paper_id),
            )
        return cursor.rowcount > 0

    def list_annotations(
        self,
        paper_id: str,
        *,
        reading_session_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if user_id is not None:
            return self._list_annotations_scoped(
                paper_id, reading_session_id=reading_session_id, user_id=user_id
            )
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
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        if user_id is not None:
            return self._upsert_annotation_scoped(
                annotation_id=annotation_id,
                paper_id=paper_id,
                reading_session_id=reading_session_id,
                annotation_type=annotation_type,
                color=color,
                page_number=page_number,
                section_id=section_id,
                selected_text=selected_text,
                rects=rects,
                note_text=note_text,
                user_id=user_id,
            )
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

    def delete_annotation(
        self, paper_id: str, annotation_id: str, *, user_id: str | None = None
    ) -> bool:
        if user_id is not None:
            with self.store._connection() as connection:
                cursor = connection.execute(
                    """DELETE FROM user_paper_annotations
                       WHERE user_id = ? AND paper_id = ? AND annotation_id = ?""",
                    (user_id, paper_id, annotation_id),
                )
            return cursor.rowcount > 0
        with self.store._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM paper_annotations WHERE paper_id = ? AND annotation_id = ?",
                (paper_id, annotation_id),
            )
        return cursor.rowcount > 0

    def _list_papers_scoped(
        self,
        *,
        search: str,
        library_only: bool,
        folder_id: str | None,
        reading_scope: str,
        limit: int,
        user_id: str,
    ) -> list[dict[str, Any]]:
        pattern = f"%{search.strip()}%"
        folder_clause = ""
        folder_parameters: list[Any] = []
        if folder_id == "__unfiled__":
            folder_clause = "AND l.folder_id IS NULL"
        elif folder_id:
            folder_clause = """AND l.folder_id IN (
                WITH RECURSIVE subtree(folder_id) AS (
                    SELECT folder_id FROM user_paper_folders
                    WHERE folder_id = ? AND user_id = ?
                    UNION ALL
                    SELECT child.folder_id FROM user_paper_folders child
                    JOIN subtree parent ON child.parent_folder_id = parent.folder_id
                    WHERE child.user_id = ?
                )
                SELECT folder_id FROM subtree
            )"""
            folder_parameters.extend([folder_id, user_id, user_id])
        if reading_scope == "reviewed":
            reading_clause = """AND EXISTS (
                SELECT 1 FROM paper_reading_sessions reviewed
                LEFT JOIN conversations reviewed_conversation
                  ON reviewed_conversation.conversation_id = reviewed.conversation_id
                WHERE reviewed.paper_id = p.paper_id
                  AND COALESCE(NULLIF(reviewed.user_id, ''), reviewed_conversation.user_id) = ?
            )"""
        elif reading_scope == "unreviewed":
            reading_clause = """AND NOT EXISTS (
                SELECT 1 FROM paper_reading_sessions reviewed
                LEFT JOIN conversations reviewed_conversation
                  ON reviewed_conversation.conversation_id = reviewed.conversation_id
                WHERE reviewed.paper_id = p.paper_id
                  AND COALESCE(NULLIF(reviewed.user_id, ''), reviewed_conversation.user_id) = ?
            )"""
        else:
            reading_clause = ""
        library_clause = "AND l.paper_id IS NOT NULL" if library_only else ""
        accessible_clause = "" if library_only else """AND (
            l.paper_id IS NOT NULL OR EXISTS (
                SELECT 1 FROM paper_reading_access access
                WHERE access.paper_id = p.paper_id AND access.user_id = ?
            )
        )"""
        params: list[Any] = [
            user_id, user_id, user_id, user_id, user_id,
            pattern, pattern, pattern,
        ]
        params.extend(folder_parameters)
        if reading_scope in {"reviewed", "unreviewed"}:
            params.append(user_id)
        if not library_only:
            params.append(user_id)
        params.append(limit)
        with self.store._connection() as connection:
            rows = connection.execute(
                f"""SELECT p.*, l.reading_status, l.note AS library_note, l.added_at,
                           l.folder_id, f.name AS folder_name,
                           (SELECT COUNT(*)
                              FROM paper_reading_sessions owned_reading
                              LEFT JOIN conversations owned_conversation
                                ON owned_conversation.conversation_id = owned_reading.conversation_id
                             WHERE owned_reading.paper_id = p.paper_id
                               AND COALESCE(NULLIF(owned_reading.user_id, ''), owned_conversation.user_id) = ?
                           ) AS reading_count,
                           (SELECT latest.reading_session_id
                              FROM paper_reading_sessions latest
                              LEFT JOIN conversations latest_conversation
                                ON latest_conversation.conversation_id = latest.conversation_id
                             WHERE latest.paper_id = p.paper_id
                               AND COALESCE(NULLIF(latest.user_id, ''), latest_conversation.user_id) = ?
                             ORDER BY latest.updated_at DESC LIMIT 1) AS latest_reading_session_id,
                           (SELECT COUNT(*) FROM user_paper_annotations owned_annotation
                             WHERE owned_annotation.paper_id = p.paper_id
                               AND owned_annotation.user_id = ?) AS annotation_count,
                           CASE WHEN d.paper_id IS NULL THEN 0 ELSE 1 END AS has_document,
                           d.document_json AS document_json
                    FROM papers p
                    LEFT JOIN user_library_items l
                      ON l.paper_id = p.paper_id AND l.user_id = ?
                    LEFT JOIN user_paper_folders f
                      ON f.folder_id = l.folder_id AND f.user_id = ?
                    LEFT JOIN paper_documents d ON d.paper_id = p.paper_id
                    WHERE (p.title LIKE ? OR p.abstract LIKE ? OR p.authors_json LIKE ?)
                    {library_clause}
                    {folder_clause}
                    {reading_clause}
                    {accessible_clause}
                    ORDER BY COALESCE(l.updated_at, p.updated_at) DESC LIMIT ?""",
                params,
            ).fetchall()
        folder_paths = {
            folder["folder_id"]: folder["path"]
            for folder in self._list_folders_scoped(user_id=user_id)
        }
        items: list[dict[str, Any]] = []
        for row in rows:
            display_title, display_abstract = _paper_display_fields(
                row["title"], row["abstract"], row["document_json"]
            )
            items.append({
                "paper_id": row["paper_id"],
                "title": display_title,
                "authors": _paper_authors(row["authors_json"]),
                "abstract": display_abstract,
                "publication_year": row["publication_year"],
                "venue": row["venue"] or "",
                "doi": row["doi"] or "",
                "arxiv_id": row["arxiv_id"] or "",
                "source_url": row["source_url"] or "",
                "in_library": row["reading_status"] is not None,
                "reading_status": row["reading_status"] or "",
                "library_note": row["library_note"] or "",
                "folder_id": row["folder_id"] or "",
                "folder_name": row["folder_name"] or "",
                "folder_path": folder_paths.get(row["folder_id"], ""),
                "reading_count": int(row["reading_count"]),
                "latest_reading_session_id": row["latest_reading_session_id"] or "",
                "annotation_count": int(row["annotation_count"]),
                "has_document": bool(row["has_document"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return items

    def _set_library_item_scoped(
        self,
        paper_id: str,
        *,
        reading_status: str,
        note: str,
        folder_id: str | None,
        user_id: str,
    ) -> bool:
        with self.store._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if exists is None:
                return False
            already_owned = connection.execute(
                "SELECT 1 FROM user_library_items WHERE user_id = ? AND paper_id = ?",
                (user_id, paper_id),
            ).fetchone() is not None
            if not already_owned and not self.store.user_can_start_paper_reading(
                paper_id, user_id
            ):
                return False
            if folder_id and connection.execute(
                "SELECT 1 FROM user_paper_folders WHERE folder_id = ? AND user_id = ?",
                (folder_id, user_id),
            ).fetchone() is None:
                return False
        self.store.add_to_library(
            paper_id,
            reading_status=reading_status,
            note=note,
            folder_id=folder_id,
            user_id=user_id,
        )
        self.store.grant_paper_reading_access(paper_id, user_id, source="library")
        return True

    def _paper_is_accessible(self, paper_id: str, user_id: str) -> bool:
        with self.store._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM user_library_items WHERE user_id = ? AND paper_id = ?",
                (user_id, paper_id),
            ).fetchone() is not None:
                return True
        return self.store.user_can_start_paper_reading(paper_id, user_id)

    def _get_paper_note_scoped(
        self, paper_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        if not self._paper_is_accessible(paper_id, user_id):
            return None
        with self.store._connection() as connection:
            paper = connection.execute(
                "SELECT title, abstract FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                return None
            note = connection.execute(
                """SELECT content_markdown, created_at, updated_at
                   FROM user_paper_notes WHERE user_id = ? AND paper_id = ?""",
                (user_id, paper_id),
            ).fetchone()
        display_title, _ = _paper_display_fields(paper["title"], paper["abstract"])
        return {
            "paper_id": paper_id,
            "paper_title": display_title,
            "format": "markdown",
            "content_markdown": note["content_markdown"] if note else "",
            "created_at": note["created_at"] if note else None,
            "updated_at": note["updated_at"] if note else None,
        }

    def _set_paper_note_scoped(
        self, paper_id: str, content_markdown: str, *, user_id: str
    ) -> dict[str, Any] | None:
        if not self._paper_is_accessible(paper_id, user_id):
            return None
        now = _now()
        with self.store._connection() as connection:
            connection.execute(
                """INSERT INTO user_paper_notes(
                       user_id, paper_id, content_markdown, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, paper_id) DO UPDATE SET
                   content_markdown = excluded.content_markdown,
                   updated_at = excluded.updated_at""",
                (user_id, paper_id, content_markdown, now, now),
            )
        return self._get_paper_note_scoped(paper_id, user_id=user_id)

    def _list_folders_scoped(self, *, user_id: str) -> list[dict[str, Any]]:
        with self.store._connection() as connection:
            rows = connection.execute(
                """WITH RECURSIVE folder_tree AS (
                       SELECT folder_id, name, parent_folder_id, created_at, updated_at,
                              name AS path, 0 AS depth
                       FROM user_paper_folders
                       WHERE user_id = ? AND parent_folder_id IS NULL
                       UNION ALL
                       SELECT child.folder_id, child.name, child.parent_folder_id,
                              child.created_at, child.updated_at,
                              tree.path || ' / ' || child.name, tree.depth + 1
                       FROM user_paper_folders child
                       JOIN folder_tree tree ON child.parent_folder_id = tree.folder_id
                       WHERE child.user_id = ?
                   )
                   SELECT tree.*, COUNT(l.paper_id) AS paper_count
                   FROM folder_tree tree
                   LEFT JOIN user_library_items l
                     ON l.folder_id = tree.folder_id AND l.user_id = ?
                   GROUP BY tree.folder_id
                   ORDER BY tree.path COLLATE NOCASE""",
                (user_id, user_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _create_folder_scoped(
        self, name: str, *, parent_folder_id: str | None, user_id: str
    ) -> dict[str, Any]:
        now = _now()
        folder_id = _id("folder")
        with self.store._connection() as connection:
            if parent_folder_id and connection.execute(
                """SELECT 1 FROM user_paper_folders
                   WHERE folder_id = ? AND user_id = ?""",
                (parent_folder_id, user_id),
            ).fetchone() is None:
                raise ValueError("上级文件夹不存在。")
            connection.execute(
                """INSERT INTO user_paper_folders(
                       folder_id, user_id, name, parent_folder_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (folder_id, user_id, name.strip(), parent_folder_id, now, now),
            )
        return {
            "folder_id": folder_id,
            "name": name.strip(),
            "parent_folder_id": parent_folder_id,
            "paper_count": 0,
        }

    def _update_folder_scoped(
        self,
        folder_id: str,
        *,
        name: str,
        parent_folder_id: str | None,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self.store._connection() as connection:
            current = connection.execute(
                "SELECT * FROM user_paper_folders WHERE folder_id = ? AND user_id = ?",
                (folder_id, user_id),
            ).fetchone()
            if current is None:
                return None
            if parent_folder_id == folder_id:
                raise ValueError("文件夹不能移动到自身。")
            if parent_folder_id:
                parent = connection.execute(
                    "SELECT 1 FROM user_paper_folders WHERE folder_id = ? AND user_id = ?",
                    (parent_folder_id, user_id),
                ).fetchone()
                if parent is None:
                    raise ValueError("目标文件夹不存在。")
                descendant = connection.execute(
                    """WITH RECURSIVE descendants(folder_id) AS (
                           SELECT folder_id FROM user_paper_folders
                           WHERE parent_folder_id = ? AND user_id = ?
                           UNION ALL
                           SELECT child.folder_id FROM user_paper_folders child
                           JOIN descendants parent ON child.parent_folder_id = parent.folder_id
                           WHERE child.user_id = ?
                       )
                       SELECT 1 FROM descendants WHERE folder_id = ?""",
                    (folder_id, user_id, user_id, parent_folder_id),
                ).fetchone()
                if descendant is not None:
                    raise ValueError("文件夹不能移动到自己的子文件夹中。")
            now = _now()
            connection.execute(
                """UPDATE user_paper_folders
                   SET name = ?, parent_folder_id = ?, updated_at = ?
                   WHERE folder_id = ? AND user_id = ?""",
                (name.strip(), parent_folder_id, now, folder_id, user_id),
            )
        return {
            "folder_id": folder_id,
            "name": name.strip(),
            "parent_folder_id": parent_folder_id,
        }

    def _delete_folder_scoped(self, folder_id: str, *, user_id: str) -> bool:
        with self.store._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM user_paper_folders WHERE folder_id = ? AND user_id = ?",
                (folder_id, user_id),
            ).fetchone()
            if exists is None:
                return False
            has_contents = connection.execute(
                """SELECT EXISTS(
                           SELECT 1 FROM user_paper_folders
                           WHERE parent_folder_id = ? AND user_id = ?)
                          OR EXISTS(
                           SELECT 1 FROM user_library_items
                           WHERE folder_id = ? AND user_id = ?) AS value""",
                (folder_id, user_id, folder_id, user_id),
            ).fetchone()
            if bool(has_contents["value"]):
                raise ValueError("文件夹不为空，请先移动其中的论文和子文件夹。")
            cursor = connection.execute(
                "DELETE FROM user_paper_folders WHERE folder_id = ? AND user_id = ?",
                (folder_id, user_id),
            )
        return cursor.rowcount > 0

    def _move_library_item_scoped(
        self, paper_id: str, *, folder_id: str | None, user_id: str
    ) -> bool:
        with self.store._connection() as connection:
            if folder_id and connection.execute(
                "SELECT 1 FROM user_paper_folders WHERE folder_id = ? AND user_id = ?",
                (folder_id, user_id),
            ).fetchone() is None:
                raise ValueError("目标文件夹不存在。")
            cursor = connection.execute(
                """UPDATE user_library_items SET folder_id = ?, updated_at = ?
                   WHERE user_id = ? AND paper_id = ?""",
                (folder_id, _now(), user_id, paper_id),
            )
        return cursor.rowcount > 0

    def _list_annotations_scoped(
        self,
        paper_id: str,
        *,
        reading_session_id: str | None,
        user_id: str,
    ) -> list[dict[str, Any]]:
        if not self._paper_is_accessible(paper_id, user_id):
            return []
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM user_paper_annotations
                   WHERE user_id = ? AND paper_id = ?
                   ORDER BY page_number, created_at""",
                (user_id, paper_id),
            ).fetchall()
        return [self._annotation(row) for row in rows]

    def _upsert_annotation_scoped(
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
        user_id: str,
    ) -> dict[str, Any] | None:
        if not self._paper_is_accessible(paper_id, user_id):
            return None
        now = _now()
        with self.store._connection() as connection:
            if reading_session_id:
                session = connection.execute(
                    """SELECT 1 FROM paper_reading_sessions reading
                       LEFT JOIN conversations owner_conversation
                         ON owner_conversation.conversation_id = reading.conversation_id
                       WHERE reading.reading_session_id = ? AND reading.paper_id = ?
                         AND COALESCE(NULLIF(reading.user_id, ''), owner_conversation.user_id) = ?""",
                    (reading_session_id, paper_id, user_id),
                ).fetchone()
                if session is None:
                    return None
            connection.execute(
                """INSERT INTO user_paper_annotations(
                       user_id, annotation_id, paper_id, reading_session_id,
                       annotation_type, color, page_number, section_id, selected_text,
                       anchor_schema_version, anchor_json, note_text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pdf-rects-v1', ?, ?, ?, ?)
                   ON CONFLICT(user_id, annotation_id) DO UPDATE SET
                       reading_session_id = excluded.reading_session_id,
                       annotation_type = excluded.annotation_type,
                       color = excluded.color,
                       page_number = excluded.page_number,
                       section_id = excluded.section_id,
                       selected_text = excluded.selected_text,
                       anchor_schema_version = excluded.anchor_schema_version,
                       anchor_json = excluded.anchor_json,
                       note_text = excluded.note_text,
                       updated_at = excluded.updated_at
                   WHERE user_paper_annotations.paper_id = excluded.paper_id""",
                (
                    user_id, annotation_id, paper_id, reading_session_id,
                    annotation_type, color, page_number, section_id, selected_text,
                    _json({"rects": rects}), note_text, now, now,
                ),
            )
            row = connection.execute(
                """SELECT * FROM user_paper_annotations
                   WHERE user_id = ? AND annotation_id = ? AND paper_id = ?""",
                (user_id, annotation_id, paper_id),
            ).fetchone()
        return self._annotation(row) if row else None

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
