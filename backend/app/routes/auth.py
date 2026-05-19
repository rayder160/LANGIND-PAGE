from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models import User, Area
from app.security import hash_password, verify_password, create_access_token, decode_token
from jose import JWTError

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Cuenta desactivada")
    return user


async def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Acceso denegado")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Acceso denegado")
    return user


@router.post("/token")
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Cuenta desactivada")
    token = create_access_token({"sub": user.id, "role": user.role, "tenant_id": user.tenant_id})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/login")
async def login_json(request: Request, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.get("email")))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.get("password", ""), user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Cuenta desactivada")
    token = create_access_token({"sub": user.id, "role": user.role, "tenant_id": user.tenant_id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "tenant_id": user.tenant_id,
            "area_id": user.area_id,
        }
    }


@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    area_name = None
    if user.area_id:
        from app.models import Area
        area_q = await db.execute(select(Area).where(Area.id == user.area_id))
        area = area_q.scalar_one_or_none()
        area_name = area.name if area else None
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "tenant_id": user.tenant_id,
        "area_id": user.area_id,
        "area_name": area_name,
        "is_active": user.is_active,
    }


class PersonalRegisterRequest(BaseModel):
    email: str
    password: str
    name: str


@router.post("/register/personal")
async def register_personal(data: PersonalRegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Registro personal — crea en un solo paso:
    1. Tenant personal
    2. Área personal ("Mi Cerebro")
    3. Usuario con rol admin
    4. Identidad del agente (IM) si CME_ENABLE_AGENT_IDENTITY=true

    No requiere configuración de empresa. El cerebro cognitivo se activa automáticamente.
    """
    from app.models.tenant import Tenant
    from app.models.area import Area
    from app.models.cme import AgentIdentity
    from app.config import settings
    from datetime import datetime, timezone
    import json

    # Verificar que el email no existe
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El email ya está registrado")

    # 1. Crear tenant personal
    tenant = Tenant(
        name=f"Personal_{data.email.split('@')[0]}",
        licenses_total=1,
        is_active=True,
        subscription_status="trial",
    )
    db.add(tenant)
    await db.flush()

    # 2. Crear área personal
    area = Area(
        tenant_id=tenant.id,
        name="Mi Cerebro",
        cme_lambda_rate=settings.CME_DEFAULT_LAMBDA,
        episode_count_since_last_detection=0,
    )
    db.add(area)
    await db.flush()

    # 3. Crear usuario
    user = User(
        tenant_id=tenant.id,
        area_id=area.id,
        email=data.email,
        name=data.name,
        hashed_password=hash_password(data.password),
        role="admin",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    # 4. Crear identidad del agente si está habilitado
    if settings.CME_ENABLE_AGENT_IDENTITY:
        identity = AgentIdentity(
            area_id=area.id,
            tenant_id=tenant.id,
            name=settings.CME_IDENTITY_DEFAULT_NAME,
            birth_date=datetime.now(timezone.utc),
            total_sessions=0,
            total_episodes=0,
            self_description=None,
            core_values=settings.CME_IDENTITY_DEFAULT_VALUES,
            is_enabled=settings.CME_IDENTITY_AUTO_ENABLE,
        )
        db.add(identity)

    await db.commit()

    # Generar token
    token = create_access_token({"sub": user.id, "role": user.role, "tenant_id": tenant.id})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "tenant_id": tenant.id,
            "area_id": area.id,
            "area_name": area.name,
        },
        "message": f"Bienvenido {data.name}. Tu cerebro cognitivo '{settings.CME_IDENTITY_DEFAULT_NAME}' está listo.",
    }
