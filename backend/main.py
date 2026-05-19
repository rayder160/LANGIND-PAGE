import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from app.database import init_db
from app.routes import auth, chat
from app.routes import superadmin
from app.routes import feedback
from app.routes.admin import router as admin_router
from app.routes.brain_control_panel import router as brain_router
from app.routes.openai_compat import router as openai_compat_router
from app.routes.tools import router as tools_router
from app.models import User, Tenant
from app.database import AsyncSessionLocal
from app.security import hash_password
from sqlalchemy import select
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_superadmin()
    yield

async def seed_superadmin():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == "admin@proxdeep.com"))
        if not result.scalar_one_or_none():
            from app.models.area import Area
            from app.models.cme import AgentIdentity
            from app.config import settings
            from datetime import datetime, timezone

            # Crear tenant personal del superadmin
            tenant = Tenant(
                name="ProxDeep Admin",
                licenses_total=999,
                is_active=True,
                subscription_status="active",
            )
            db.add(tenant)
            await db.flush()

            # Crear área personal
            area = Area(
                tenant_id=tenant.id,
                name="Mi Cerebro",
                cme_lambda_rate=settings.CME_DEFAULT_LAMBDA,
                episode_count_since_last_detection=0,
            )
            db.add(area)
            await db.flush()

            # Crear superadmin con tenant y área
            user = User(
                email="admin@proxdeep.com",
                name="ProxDeep Admin",
                hashed_password=hash_password("proxdeep2026"),
                role="superadmin",
                is_active=True,
                tenant_id=tenant.id,
                area_id=area.id,
            )
            db.add(user)
            await db.flush()

            # Crear identidad del agente si está habilitado
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

app = FastAPI(title="ProxDeep API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(superadmin.router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(brain_router, prefix="/api")
app.include_router(openai_compat_router)  # sin prefix /api — vive en /v1/chat/completions
app.include_router(tools_router)          # MCP tools — vive en /api/tools/exec

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ProxDeep API", "version": "2.0.0"}

@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/index.html")

# Frontend — servido en root
frontend_path = os.path.join(os.path.dirname(__file__), "..")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
