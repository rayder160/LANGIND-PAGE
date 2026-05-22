from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from app.database import get_db
from app.models import User, Area, AreaDocument
from app.routes.auth import get_current_user

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def search(q: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = q.strip().lower()
    if len(query) < 3:
        return {"results": []}

    search_pattern = f"%{query}%"
    results = []

    doc_stmt = select(AreaDocument, Area).join(Area, Area.id == AreaDocument.area_id)
    if user.role == "leader":
        doc_stmt = doc_stmt.where(AreaDocument.area_id == user.area_id)
    else:
        doc_stmt = doc_stmt.where(AreaDocument.tenant_id == user.tenant_id)
    doc_stmt = doc_stmt.where(func.lower(AreaDocument.filename).like(search_pattern)).limit(20)
    docs = await db.execute(doc_stmt)
    for doc, area in docs.all():
        results.append({
            "type": "Documento",
            "title": doc.filename,
            "subtitle": area.name or "Área desconocida",
            "detail": f"Documento indexado en {area.name}",
            "path": "workspace"
        })

    user_stmt = select(User).where(
        User.tenant_id == user.tenant_id,
        User.role == "user",
        or_(func.lower(User.name).like(search_pattern), func.lower(User.email).like(search_pattern))
    )
    if user.role == "leader":
        user_stmt = user_stmt.where(User.area_id == user.area_id)
    user_stmt = user_stmt.limit(20)
    users = await db.execute(user_stmt)
    for u in users.scalars().all():
        results.append({
            "type": "Usuario",
            "title": u.name,
            "subtitle": u.email,
            "detail": u.area_id or "Área no asignada",
            "path": "users"
        })

    return {"results": results}
