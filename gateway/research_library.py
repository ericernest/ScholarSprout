"""HTTP API for the local research library and PDF annotations."""

from __future__ import annotations

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
    rects: list[PdfRect] = Field(min_length=1, max_length=300)
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


@router.get("/paper-readings")
def paper_readings(request: Request, search: Search = "", limit: Limit = 100) -> list[dict]:
    return _catalog(request).list_paper_readings(search=search, limit=limit)


@router.get("/papers")
def papers(
    request: Request,
    search: Search = "",
    library_only: bool = True,
    limit: Limit = 100,
) -> list[dict]:
    return _catalog(request).list_papers(
        search=search, library_only=library_only, limit=limit
    )


@router.put("/papers/{paper_id}/library")
def save_library_item(paper_id: str, payload: LibraryItemUpdate, request: Request) -> dict:
    if not _catalog(request).set_library_item(
        paper_id, reading_status=payload.reading_status, note=payload.note.strip()
    ):
        raise HTTPException(status_code=404, detail="论文不存在。")
    return {"paper_id": paper_id, "saved": True}


@router.delete("/papers/{paper_id}/library")
def delete_library_item(paper_id: str, request: Request) -> dict:
    if not _catalog(request).remove_library_item(paper_id):
        raise HTTPException(status_code=404, detail="论文不在论文库中。")
    return {"paper_id": paper_id, "removed": True}


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
