"""HTTP API for the local research library and PDF annotations."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from storage import LocalResearchStore, ResearchCatalog

router = APIRouter(prefix="/api/research", tags=["research-library"])
Search = Annotated[str, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=200)]


class LibraryItemUpdate(BaseModel):
    reading_status: Literal["unread", "reading", "read", "archived"] = "unread"
    note: str = Field(default="", max_length=4000)
    folder_id: str | None = Field(default=None, max_length=180)


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_folder_id: str | None = Field(default=None, max_length=180)


class FolderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_folder_id: str | None = Field(default=None, max_length=180)


class PaperFolderAssignment(BaseModel):
    folder_id: str | None = Field(default=None, max_length=180)


class PaperNoteUpdate(BaseModel):
    content_markdown: str = Field(default="", max_length=1_000_000)


class PdfRect(BaseModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def fit_page(self) -> "PdfRect":
        if self.left + self.width > 1.01 or self.top + self.height > 1.01:
            raise ValueError("高亮坐标超出 PDF 页面")
        return self


class AnnotationUpdate(BaseModel):
    reading_session_id: str | None = Field(default=None, max_length=180)
    annotation_type: Literal["highlight", "note"]
    color: Literal["yellow", "green", "blue", "pink"] = "yellow"
    page_number: int = Field(ge=1)
    section_id: str | None = Field(default=None, max_length=500)
    selected_text: str = Field(min_length=1, max_length=10000)
    # AI reflow selections start as text anchors. Their PDF rectangles are
    # resolved lazily when the matching PDF text layer is rendered.
    rects: list[PdfRect] = Field(default_factory=list, max_length=300)
    note_text: str = Field(default="", max_length=20000)


def _catalog(request: Request) -> ResearchCatalog:
    store = getattr(request.app.state, "research_storage", None)
    if not isinstance(store, LocalResearchStore):
        raise HTTPException(status_code=503, detail="研究资料库尚未初始化。")
    return ResearchCatalog(store)


@router.get("/summary")
def research_summary(request: Request) -> dict[str, int]:
    return _catalog(request).counts()


@router.get("/conversations")
def conversations(request: Request, search: Search = "", limit: Limit = 100) -> list[dict]:
    return _catalog(request).list_conversations(search=search, limit=limit)


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, request: Request) -> dict:
    item = _catalog(request).get_conversation(conversation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="会话不存在。")
    return item


@router.get("/domain-onboardings")
def domain_onboardings(request: Request, search: Search = "", limit: Limit = 100) -> list[dict]:
    return _catalog(request).list_domain_onboardings(search=search, limit=limit)


@router.get("/domain-onboardings/{artifact_id}")
def domain_onboarding_detail(artifact_id: str, request: Request) -> dict:
    item = _catalog(request).get_domain_onboarding(artifact_id)
    if item is None:
        raise HTTPException(status_code=404, detail="领域入门记录不存在。")
    return item


@router.get("/domain-onboardings/{artifact_id}/workspace")
def domain_onboarding_workspace(artifact_id: str, request: Request) -> dict:
    """Return a workbench snapshot for an active job or a persisted artifact."""
    item = _catalog(request).get_domain_onboarding(artifact_id)
    if item is None:
        raise HTTPException(status_code=404, detail="领域入门记录不存在。")
    job_store = getattr(request.app.state, "domain_onboarding_job_store", None)
    manager = getattr(request.app.state, "domain_onboarding_job_manager", None)
    job = job_store.get(artifact_id) if job_store is not None else None
    if job is not None and manager is not None:
        return {
            **job,
            "access_token": manager.access_token(artifact_id),
            "workspace_source": "job",
        }

    overview = item.get("overview") if isinstance(item.get("overview"), dict) else {}
    result = {
        **overview,
        "schema_version": item.get("output_schema_version") or "",
        "query": item.get("query") or "",
        "learner_profile": item.get("learner_profile") or {},
        "research_plan": item.get("research_plan") or {},
        "learning_path": item.get("learning_path") or [],
        "knowledge_graph": item.get("knowledge_graph") or {},
        "papers": [
            {
                **paper,
                "year": paper.get("publication_year"),
                "url": paper.get("source_url") or "",
                "contribution": paper.get("reason") or "",
            }
            for paper in item.get("recommendations") or []
        ],
    }
    return {
        "task_id": artifact_id,
        "state": item.get("state") or "completed",
        "current_stage": item.get("current_stage") or "completed",
        "progress": 1.0 if item.get("state") == "completed" else 0.0,
        "request": {
            "query": item.get("query") or "",
            "session_id": item.get("conversation_id") or "",
        },
        "result": result,
        "error": item.get("error_summary") or None,
        "retryable": False,
        "workspace_source": "catalog",
    }


@router.get("/paper-readings")
def paper_readings(request: Request, search: Search = "", limit: Limit = 100) -> list[dict]:
    return _catalog(request).list_paper_readings(search=search, limit=limit)


@router.get("/papers")
def papers(
    request: Request,
    search: Search = "",
    library_only: bool = True,
    folder_id: str | None = Query(default=None, max_length=180),
    reading_scope: Literal["all", "reviewed", "unreviewed"] = "all",
    limit: Limit = 100,
) -> list[dict]:
    return _catalog(request).list_papers(
        search=search,
        library_only=library_only,
        folder_id=folder_id,
        reading_scope=reading_scope,
        limit=limit,
    )


@router.get("/paper-folders")
def paper_folders(request: Request) -> list[dict]:
    return _catalog(request).list_folders()


@router.post("/paper-folders", status_code=201)
def create_paper_folder(payload: FolderCreate, request: Request) -> dict:
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="文件夹名称不能为空。")
    try:
        return _catalog(request).create_folder(
            payload.name.strip(), parent_folder_id=payload.parent_folder_id
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="同一目录下已经存在同名文件夹。") from error


@router.patch("/paper-folders/{folder_id}")
def update_paper_folder(
    folder_id: str, payload: FolderUpdate, request: Request
) -> dict:
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="文件夹名称不能为空。")
    try:
        item = _catalog(request).update_folder(
            folder_id,
            name=payload.name.strip(),
            parent_folder_id=payload.parent_folder_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="目标目录下已经存在同名文件夹。") from error
    if item is None:
        raise HTTPException(status_code=404, detail="文件夹不存在。")
    return item


@router.delete("/paper-folders/{folder_id}")
def delete_paper_folder(folder_id: str, request: Request) -> dict:
    try:
        deleted = _catalog(request).delete_folder(folder_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="文件夹不存在。")
    return {"folder_id": folder_id, "deleted": True}


@router.put("/papers/{paper_id}/library")
def save_library_item(paper_id: str, payload: LibraryItemUpdate, request: Request) -> dict:
    if not _catalog(request).set_library_item(
        paper_id,
        reading_status=payload.reading_status,
        note=payload.note.strip(),
        folder_id=payload.folder_id,
    ):
        raise HTTPException(status_code=404, detail="论文不存在。")
    return {"paper_id": paper_id, "saved": True}


@router.post("/papers/{paper_id}/reading-session", status_code=201)
def create_paper_reading_session(paper_id: str, request: Request) -> dict:
    """Create and persist a reading session before opening the workbench."""
    store = getattr(request.app.state, "research_storage", None)
    session_manager = getattr(request.app.state, "session_manager", None)
    paper_storage = getattr(request.app.state, "paper_storage", None)
    if not isinstance(store, LocalResearchStore) or session_manager is None:
        raise HTTPException(status_code=503, detail="论文精读服务尚未初始化。")
    with store._connection() as connection:
        paper = connection.execute(
            "SELECT title FROM papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在。")
    if paper_storage is None or paper_storage.load_paper(paper_id) is None:
        raise HTTPException(status_code=409, detail="请先导入或上传这篇论文的 PDF。")
    session = session_manager.create_session(
        paper_id=paper_id,
        paper_title=str(paper["title"] or paper_id),
        user_id="default",
    )
    store.ensure_library_item(paper_id, reading_status="reading")
    return {
        "paper_id": paper_id,
        "reading_session_id": session.session_id,
        "state": session.state,
    }


@router.delete("/papers/{paper_id}/library")
def delete_library_item(paper_id: str, request: Request) -> dict:
    if not _catalog(request).remove_library_item(paper_id):
        raise HTTPException(status_code=404, detail="论文不在论文库中。")
    return {"paper_id": paper_id, "removed": True}


@router.patch("/papers/{paper_id}/folder")
def move_paper_to_folder(
    paper_id: str, payload: PaperFolderAssignment, request: Request
) -> dict:
    try:
        moved = _catalog(request).move_library_item(
            paper_id, folder_id=payload.folder_id
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not moved:
        raise HTTPException(status_code=404, detail="论文不在论文管理中。")
    return {"paper_id": paper_id, "folder_id": payload.folder_id, "moved": True}


@router.get("/papers/{paper_id}/note")
def paper_note(paper_id: str, request: Request) -> dict:
    item = _catalog(request).get_paper_note(paper_id)
    if item is None:
        raise HTTPException(status_code=404, detail="论文不存在。")
    return item


@router.put("/papers/{paper_id}/note")
def save_paper_note(paper_id: str, payload: PaperNoteUpdate, request: Request) -> dict:
    item = _catalog(request).set_paper_note(paper_id, payload.content_markdown)
    if item is None:
        raise HTTPException(status_code=404, detail="论文不存在。")
    return item


@router.get("/papers/{paper_id}/annotations")
def annotations(
    paper_id: str,
    request: Request,
    reading_session_id: str | None = Query(default=None, max_length=180),
) -> list[dict]:
    return _catalog(request).list_annotations(
        paper_id, reading_session_id=reading_session_id
    )


@router.put("/papers/{paper_id}/annotations/{annotation_id}")
def save_annotation(
    paper_id: str,
    annotation_id: str,
    payload: AnnotationUpdate,
    request: Request,
) -> dict:
    if not annotation_id.strip() or len(annotation_id) > 180:
        raise HTTPException(status_code=422, detail="标注 ID 无效。")
    item = _catalog(request).upsert_annotation(
        annotation_id=annotation_id,
        paper_id=paper_id,
        reading_session_id=payload.reading_session_id,
        annotation_type=payload.annotation_type,
        color=payload.color,
        page_number=payload.page_number,
        section_id=payload.section_id,
        selected_text=payload.selected_text.strip(),
        rects=[rect.model_dump() for rect in payload.rects],
        note_text=payload.note_text.strip(),
    )
    if item is None:
        raise HTTPException(status_code=404, detail="论文不存在。")
    return item


@router.delete("/papers/{paper_id}/annotations/{annotation_id}")
def delete_annotation(paper_id: str, annotation_id: str, request: Request) -> dict:
    if not _catalog(request).delete_annotation(paper_id, annotation_id):
        raise HTTPException(status_code=404, detail="标注不存在。")
    return {"annotation_id": annotation_id, "deleted": True}
