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


class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token (JWT) obtenido desde Google Identity Services


@router.post("/google")
async def login_google(data: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    Autenticación con Google.
    Flujo:
      1. Frontend obtiene credential (ID token) de Google Identity Services
      2. Frontend envía { credential } a este endpoint
      3. Backend valida el token con Google
      4. Si el usuario existe → devuelve sesión normal
      5. Si no existe → crea usuario automáticamente con rol 'employee'

    TODO BACKEND — Configuración requerida en .env:
      GOOGLE_CLIENT_ID=561417509211-20581mufa4ndkkdmlktm822eiu9vd57g.apps.googleusercontent.com

    TODO BACKEND — Instalar dependencia:
      pip install google-auth

    TODO BACKEND — Configurar en Google Cloud Console:
      APIs & Services → Credentials → OAuth 2.0 Client ID
      Authorized JavaScript origins:
        - http://localhost
        - http://localhost:8000
        - https://tu-dominio-de-produccion.com
    """
    import os
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Google Auth no está instalado en el servidor. Ejecuta: pip install google-auth"
        )

    google_client_id = os.getenv(
        "GOOGLE_CLIENT_ID",
        "561417509211-20581mufa4ndkkdmlktm822eiu9vd57g.apps.googleusercontent.com"
    )

    try:
        id_info = id_token.verify_oauth2_token(
            data.credential,
            google_requests.Request(),
            google_client_id
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Token de Google inválido: {str(e)}")

    email = id_info.get("email")
    name = id_info.get("name", email.split("@")[0] if email else "Usuario")

    if not email:
        raise HTTPException(status_code=400, detail="No se pudo obtener el email de Google")

    # Buscar usuario existente
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        # Crear usuario nuevo con rol employee
        # TODO BACKEND: Asignar tenant y área según dominio del email o configuración
        from app.models.tenant import Tenant
        from app.models.area import Area
        from app.config import settings
        from datetime import datetime, timezone

        tenant = Tenant(
            name=f"Google_{email.split('@')[1]}",
            licenses_total=1,
            is_active=True,
            subscription_status="trial",
        )
        db.add(tenant)
        await db.flush()

        area = Area(
            tenant_id=tenant.id,
            name="General",
            cme_lambda_rate=settings.CME_DEFAULT_LAMBDA,
            episode_count_since_last_detection=0,
        )
        db.add(area)
        await db.flush()

        user = User(
            tenant_id=tenant.id,
            area_id=area.id,
            email=email,
            name=name,
            hashed_password=hash_password(f"google_{email}"),  # password inutilizable
            role="employee",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        area_name = area.name
    else:
        if not user.is_active:
            raise HTTPException(status_code=401, detail="Cuenta desactivada")
        # Obtener area_name
        area_name = None
        if user.area_id:
            area_q = await db.execute(select(Area).where(Area.id == user.area_id))
            area_obj = area_q.scalar_one_or_none()
            area_name = area_obj.name if area_obj else None

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
            "area_name": area_name,
            "is_active": user.is_active,
        }
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
