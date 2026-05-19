from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from app.database import get_db
from app.models import Tenant, Area, User
from app.routes.auth import require_admin
from app.security import hash_password

router = APIRouter()


class UserCreate(BaseModel):
    email: str
    name: str
    password: str
    area_id: str | None = None


class UserUpdate(BaseModel):
    name: str | None = None
    area_id: str | None = None
    is_active: bool | None = None


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(User).where(User.tenant_id == user.tenant_id, User.role == "user"))
    users = result.scalars().all()
    out = []
    for u in users:
        area_name = None
        if u.area_id:
            area_q = await db.execute(select(Area).where(Area.id == u.area_id))
            area = area_q.scalar_one_or_none()
            area_name = area.name if area else None
        out.append({"id": u.id, "email": u.email, "name": u.name, "area_id": u.area_id, "area_name": area_name, "is_active": u.is_active, "created_at": str(u.created_at)})
    return out


@router.post("/users", status_code=201)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    tenant_q = await db.execute(select(Tenant).where(Tenant.id == admin.tenant_id))
    tenant = tenant_q.scalar_one_or_none()
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=400, detail="Tenant inactivo")

    used_q = await db.execute(select(func.count()).where(User.tenant_id == admin.tenant_id, User.role == "user", User.is_active == True))
    if (used_q.scalar() or 0) >= tenant.licenses_total:
        raise HTTPException(status_code=400, detail="Límite de licencias alcanzado")

    if (await db.execute(select(User).where(User.email == data.email))).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    if data.area_id:
        if not (await db.execute(select(Area).where(Area.id == data.area_id, Area.tenant_id == admin.tenant_id))).scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Área no válida")

    new_user = User(tenant_id=admin.tenant_id, area_id=data.area_id, email=data.email, name=data.name, hashed_password=hash_password(data.password), role="user", is_active=True)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return {"id": new_user.id, "email": new_user.email, "name": new_user.name}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, data: UserUpdate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    result = await db.execute(select(User).where(User.id == user_id, User.tenant_id == admin.tenant_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if data.area_id is not None:
        if not (await db.execute(select(Area).where(Area.id == data.area_id, Area.tenant_id == admin.tenant_id))).scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Área no válida")
        target.area_id = data.area_id

    if data.name is not None:
        target.name = data.name
    if data.is_active is not None:
        target.is_active = data.is_active

    await db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    result = await db.execute(select(User).where(User.id == user_id, User.tenant_id == admin.tenant_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    await db.delete(target)
    await db.commit()
    return {"ok": True}
