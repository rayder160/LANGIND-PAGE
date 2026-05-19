from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from app.database import get_db
from app.models import Tenant, User, ChatSession, ChatMessage
from app.routes.auth import require_superadmin
from app.security import hash_password
from app.billing import (
    subscription_summary, check_and_update_status,
    activate_subscription, suspend_tenant, cancel_tenant
)
from app.analytics import get_user_kpis, get_area_kpis

router = APIRouter(prefix="/superadmin", tags=["superadmin"])


class TenantCreate(BaseModel):
    name: str
    admin_email: str
    admin_name: str
    admin_password: str
    licenses: int = 5
    billing_cycle: str = "monthly"  # monthly | annual


class TenantUpdate(BaseModel):
    name: str | None = None
    licenses: int | None = None


class ActivatePayload(BaseModel):
    billing_cycle: str = "monthly"  # monthly | annual


async def _tenant_out(t: Tenant, db: AsyncSession) -> dict:
    """Serializa un tenant con licencias en uso y estado de suscripción."""
    await check_and_update_status(t, db)
    used_q = await db.execute(
        select(func.count()).where(User.tenant_id == t.id, User.role == "user", User.is_active == True)
    )
    used = used_q.scalar() or 0
    return {
        "id": t.id,
        "name": t.name,
        "api_key": t.api_key,
        "licenses_total": t.licenses_total,
        "licenses_used": used,
        "is_active": t.is_active,
        "created_at": str(t.created_at),
        **subscription_summary(t),
    }


@router.get("/tenants")
async def list_tenants(db: AsyncSession = Depends(get_db), _=Depends(require_superadmin)):
    result = await db.execute(select(Tenant))
    tenants = result.scalars().all()
    return [await _tenant_out(t, db) for t in tenants]


@router.post("/tenants", status_code=201)
async def create_tenant(data: TenantCreate, db: AsyncSession = Depends(get_db), _=Depends(require_superadmin)):
    existing = await db.execute(select(Tenant).where(Tenant.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya existe un tenant con ese nombre")

    tenant = Tenant(
        name=data.name,
        licenses_total=data.licenses,
        billing_cycle=data.billing_cycle,
        subscription_status="trial",
    )
    db.add(tenant)
    await db.flush()

    admin = User(
        tenant_id=tenant.id,
        email=data.admin_email,
        name=data.admin_name,
        hashed_password=hash_password(data.admin_password),
        role="admin",
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(tenant)

    return await _tenant_out(tenant, db)


@router.patch("/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, data: TenantUpdate, db: AsyncSession = Depends(get_db), _=Depends(require_superadmin)):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    if data.licenses is not None:
        used_q = await db.execute(
            select(func.count()).where(User.tenant_id == tenant_id, User.role == "user", User.is_active == True)
        )
        used = used_q.scalar() or 0
        if data.licenses < used:
            raise HTTPException(status_code=400, detail=f"No puedes reducir a {data.licenses} licencias, hay {used} en uso")
        tenant.licenses_total = data.licenses

    if data.name is not None:
        tenant.name = data.name

    await db.commit()
    return {"ok": True}


# ── Gestión de suscripción ──────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/activate")
async def activate_tenant(tenant_id: str, payload: ActivatePayload, db: AsyncSession = Depends(get_db), _=Depends(require_superadmin)):
    """Activa o renueva la suscripción después de un pago."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    if tenant.subscription_status == "cancelled":
        raise HTTPException(status_code=400, detail="Tenant cancelado. Crea uno nuevo.")

    summary = await activate_subscription(tenant, payload.billing_cycle, db)
    return {"ok": True, **summary}


@router.post("/tenants/{tenant_id}/suspend")
async def suspend_tenant_route(tenant_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_superadmin)):
    """Suspende manualmente un tenant (no pago, incumplimiento, etc.)"""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    await suspend_tenant(tenant, db)
    return {"ok": True, "status": "suspended"}


@router.post("/tenants/{tenant_id}/cancel")
async def cancel_tenant_route(tenant_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_superadmin)):
    """Cancela definitivamente un tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    await cancel_tenant(tenant, db)
    return {"ok": True, "status": "cancelled"}


# ── Stats globales ──────────────────────────────────────────────────────────

@router.get("/stats")
async def global_stats(db: AsyncSession = Depends(get_db), _=Depends(require_superadmin)):
    tenants_q = await db.execute(select(func.count()).select_from(Tenant))
    active_q = await db.execute(select(func.count()).where(Tenant.subscription_status == "active"))
    trial_q = await db.execute(select(func.count()).where(Tenant.subscription_status == "trial"))
    suspended_q = await db.execute(select(func.count()).where(Tenant.subscription_status == "suspended"))
    users_q = await db.execute(select(func.count()).where(User.role == "user", User.is_active == True))
    sessions_q = await db.execute(select(func.count()).select_from(ChatSession))
    messages_q = await db.execute(select(func.count()).select_from(ChatMessage))
    return {
        "total_tenants": tenants_q.scalar() or 0,
        "active_tenants": active_q.scalar() or 0,
        "trial_tenants": trial_q.scalar() or 0,
        "suspended_tenants": suspended_q.scalar() or 0,
        "active_users": users_q.scalar() or 0,
        "total_sessions": sessions_q.scalar() or 0,
        "total_messages": messages_q.scalar() or 0,
    }
