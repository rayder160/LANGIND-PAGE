from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Area, User
from app.routes.auth import require_admin
from app.analytics import get_user_kpis
from app.advanced_analytics import get_heatmap, get_knowledge_gaps, get_area_comparison

router = APIRouter()


@router.get("/analytics/users")
async def users_analytics(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(User).where(User.tenant_id == user.tenant_id, User.role == "user"))
    users = result.scalars().all()
    out = []
    for u in users:
        kpis = await get_user_kpis(u.id, db)
        kpis["name"] = u.name
        kpis["email"] = u.email
        out.append(kpis)
    return out


@router.get("/analytics/comparison")
async def areas_comparison(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    return await get_area_comparison(user.tenant_id, db)


@router.get("/analytics/heatmap/{area_id}")
async def area_heatmap(area_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    area_q = await db.execute(select(Area).where(Area.id == area_id, Area.tenant_id == user.tenant_id))
    if not area_q.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Área no encontrada")
    return await get_heatmap(area_id, db)


@router.get("/analytics/gaps/{area_id}")
async def area_gaps(area_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    area_q = await db.execute(select(Area).where(Area.id == area_id, Area.tenant_id == user.tenant_id))
    if not area_q.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Área no encontrada")
    return await get_knowledge_gaps(area_id, db)


@router.get("/analytics/learning/{area_id}")
async def area_learning(area_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    """Retorna qué ha aprendido el modelo sobre un área."""
    from app.models import Area, AreaChunk
    area_q = await db.execute(select(Area).where(Area.id == area_id, Area.tenant_id == user.tenant_id))
    area = area_q.scalar_one_or_none()
    if not area:
        raise HTTPException(status_code=404, detail="Área no encontrada")

    # Contar chunks por fuente
    chunks_q = await db.execute(
        select(AreaChunk.source, func.count(AreaChunk.id).label("count"))
        .where(AreaChunk.area_id == area_id)
        .group_by(AreaChunk.source)
    )
    chunks_by_source = {row.source: row.count for row in chunks_q.fetchall()}

    return {
        "area_name": area.name,
        "has_long_term_memory": bool(area.memory),
        "has_recent_memory": bool(area.memory_recent),
        "long_term_memory": area.memory,
        "recent_memory": area.memory_recent,
        "memory_updated_at": str(area.memory_updated_at) if area.memory_updated_at else None,
        "chunks_from_conversations": chunks_by_source.get("conversation", 0),
        "chunks_from_documents": sum(v for k, v in chunks_by_source.items() if k.startswith("document:")),
        "chunks_validated": chunks_by_source.get("validated", 0),
        "total_chunks": sum(chunks_by_source.values()),
        "learning_stage": (
            "avanzado" if area.memory and sum(chunks_by_source.values()) > 20
            else "en progreso" if area.memory_recent or sum(chunks_by_source.values()) > 5
            else "iniciando"
        )
    }
