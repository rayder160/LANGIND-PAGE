from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models import User, WorkspaceDocument
from app.routes.auth import get_current_user

router = APIRouter(prefix="/workspace", tags=["workspace"])


class WorkspaceDocumentCreate(BaseModel):
    title: str
    content: str


@router.get("/documents")
async def get_workspace_document(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WorkspaceDocument)
        .where(WorkspaceDocument.user_id == user.id)
        .order_by(WorkspaceDocument.updated_at.desc())
        .limit(1)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        return {"id": None, "title": "", "content": "", "updated_at": None}
    return {
        "id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "updated_at": str(doc.updated_at)
    }


@router.post("/documents")
async def save_workspace_document(data: WorkspaceDocumentCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WorkspaceDocument)
        .where(WorkspaceDocument.user_id == user.id)
        .order_by(WorkspaceDocument.updated_at.desc())
        .limit(1)
    )
    doc = result.scalar_one_or_none()
    if doc:
        doc.title = data.title
        doc.content = data.content
    else:
        doc = WorkspaceDocument(
            user_id=user.id,
            tenant_id=user.tenant_id,
            area_id=user.area_id,
            title=data.title,
            content=data.content,
        )
        db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return {
        "id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "updated_at": str(doc.updated_at)
    }
