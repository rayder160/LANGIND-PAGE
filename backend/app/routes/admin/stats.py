from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models import Tenant, Area, User, ChatSession, ChatMessage
from app.routes.auth import require_admin

router = APIRouter()


@router.get("/stats")
async def tenant_stats(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    tenant_q = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = tenant_q.scalar_one_or_none()

    used = (await db.execute(select(func.count()).where(User.tenant_id == user.tenant_id, User.role == "user", User.is_active == True))).scalar() or 0
    total_users = (await db.execute(select(func.count()).where(User.tenant_id == user.tenant_id, User.role == "user"))).scalar() or 0
    total_areas = (await db.execute(select(func.count()).where(Area.tenant_id == user.tenant_id))).scalar() or 0
    total_sessions = (await db.execute(select(func.count()).where(ChatSession.tenant_id == user.tenant_id))).scalar() or 0
    total_messages = (await db.execute(
        select(func.count(ChatMessage.id)).join(ChatSession, ChatMessage.session_id == ChatSession.id).where(ChatSession.tenant_id == user.tenant_id)
    )).scalar() or 0

    return {
        "tenant_name": tenant.name if tenant else "",
        "licenses_total": tenant.licenses_total if tenant else 0,
        "licenses_used": used,
        "licenses_free": (tenant.licenses_total - used) if tenant else 0,
        "total_users": total_users,
        "total_areas": total_areas,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
    }
