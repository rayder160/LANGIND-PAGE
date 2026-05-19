from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from app.database import get_db
from app.models import Area, User, AreaDocument
from app.routes.auth import require_admin
from app.documents import process_document

router = APIRouter()


class AreaCreate(BaseModel):
    name: str


class AreaUpdate(BaseModel):
    name: str | None = None


@router.get("/areas")
async def list_areas(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(Area).where(Area.tenant_id == user.tenant_id))
    areas = result.scalars().all()
    out = []
    for a in areas:
        count_q = await db.execute(
            select(func.count()).where(User.area_id == a.id, User.is_active == True)
        )
        out.append({
            "id": a.id,
            "name": a.name,
            "memory": bool(a.memory),
            "user_count": count_q.scalar() or 0,
            "created_at": str(a.created_at),
        })
    return out


@router.post("/areas", status_code=201)
async def create_area(data: AreaCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    area = Area(tenant_id=user.tenant_id, name=data.name)
    db.add(area)
    await db.commit()
    await db.refresh(area)
    return {"id": area.id, "name": area.name}


@router.patch("/areas/{area_id}")
async def update_area(area_id: str, data: AreaUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(Area).where(Area.id == area_id, Area.tenant_id == user.tenant_id))
    area = result.scalar_one_or_none()
    if not area:
        raise HTTPException(status_code=404, detail="Área no encontrada")
    if data.name is not None:
        area.name = data.name
    await db.commit()
    return {"ok": True}


@router.delete("/areas/{area_id}")
async def delete_area(area_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(Area).where(Area.id == area_id, Area.tenant_id == user.tenant_id))
    area = result.scalar_one_or_none()
    if not area:
        raise HTTPException(status_code=404, detail="Área no encontrada")
    await db.delete(area)
    await db.commit()
    return {"ok": True}


# ── Documentos del área ────────────────────────────────────

@router.post("/areas/{area_id}/documents")
async def upload_document(area_id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    area_q = await db.execute(select(Area).where(Area.id == area_id, Area.tenant_id == user.tenant_id))
    if not area_q.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Área no encontrada")

    filename = file.filename or "documento"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("pdf", "txt"):
        raise HTTPException(status_code=400, detail="Solo se aceptan PDF o TXT")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Máximo 10MB")

    try:
        chunk_count = await process_document(area_id, filename, content, ext, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    doc = AreaDocument(area_id=area_id, tenant_id=user.tenant_id, filename=filename, file_type=ext, chunk_count=chunk_count)
    db.add(doc)
    await db.commit()
    return {"ok": True, "filename": filename, "chunks_indexed": chunk_count}


@router.get("/areas/{area_id}/documents")
async def list_documents(area_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    area_q = await db.execute(select(Area).where(Area.id == area_id, Area.tenant_id == user.tenant_id))
    if not area_q.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Área no encontrada")
    docs_q = await db.execute(select(AreaDocument).where(AreaDocument.area_id == area_id).order_by(AreaDocument.created_at.desc()))
    docs = docs_q.scalars().all()
    return [{"id": d.id, "filename": d.filename, "file_type": d.file_type, "chunks": d.chunk_count, "created_at": str(d.created_at)} for d in docs]


@router.delete("/areas/{area_id}/documents/{doc_id}")
async def delete_document(area_id: str, doc_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    doc_q = await db.execute(select(AreaDocument).where(AreaDocument.id == doc_id, AreaDocument.area_id == area_id, AreaDocument.tenant_id == user.tenant_id))
    doc = doc_q.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    await db.delete(doc)
    await db.commit()
    return {"ok": True}
