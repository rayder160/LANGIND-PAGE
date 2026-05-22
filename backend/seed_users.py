"""
seed_users.py — Crea usuarios de prueba para todos los roles
Ejecutar: python seed_users.py  (con el venv activado, desde la carpeta backend)

Usuarios creados:
  superadmin  admin@proxdeep.com        proxdeep2026   (ya existe del seed principal)
  admin       ceo@proxdeep.com          ceo2026
  leader      jefe@proxdeep.com         jefe2026
  employee    empleado@proxdeep.com     empleado2026
"""

import asyncio
from app.database import init_db, AsyncSessionLocal
from app.models import User, Tenant
from app.models.area import Area
from app.models.cme import AgentIdentity
from app.security import hash_password
from app.config import settings
from sqlalchemy import select
from datetime import datetime, timezone


USERS_TO_SEED = [
    {
        "email": "ceo@proxdeep.com",
        "name": "CEO ProxDeep",
        "password": "ceo2026",
        "role": "ceo",
        "area_name": "Gerencia",
    },
    {
        "email": "jefe@proxdeep.com",
        "name": "Jefe de Área",
        "password": "jefe2026",
        "role": "leader",
        "area_name": "Operaciones",
    },
    {
        "email": "empleado@proxdeep.com",
        "name": "Empleado Demo",
        "password": "empleado2026",
        "role": "employee",
        "area_name": "Soporte",
    },
]


async def seed():
    await init_db()

    async with AsyncSessionLocal() as db:
        # Reusar el tenant del superadmin para todos los usuarios demo
        result = await db.execute(select(User).where(User.email == "admin@proxdeep.com"))
        superadmin = result.scalar_one_or_none()

        if not superadmin:
            print("ERROR: El superadmin no existe. Levanta el servidor primero para que se cree el seed principal.")
            return

        tenant_id = superadmin.tenant_id

        for u in USERS_TO_SEED:
            # Verificar si ya existe
            existing = await db.execute(select(User).where(User.email == u["email"]))
            if existing.scalar_one_or_none():
                print(f"  [SKIP] {u['email']} ya existe.")
                continue

            # Crear área para este usuario
            area = Area(
                tenant_id=tenant_id,
                name=u["area_name"],
                cme_lambda_rate=settings.CME_DEFAULT_LAMBDA,
                episode_count_since_last_detection=0,
            )
            db.add(area)
            await db.flush()

            # Crear usuario
            user = User(
                email=u["email"],
                name=u["name"],
                hashed_password=hash_password(u["password"]),
                role=u["role"],
                is_active=True,
                tenant_id=tenant_id,
                area_id=area.id,
            )
            db.add(user)
            await db.flush()

            # Crear identidad del agente si está habilitado
            if settings.CME_ENABLE_AGENT_IDENTITY:
                identity = AgentIdentity(
                    area_id=area.id,
                    tenant_id=tenant_id,
                    name=settings.CME_IDENTITY_DEFAULT_NAME,
                    birth_date=datetime.now(timezone.utc),
                    total_sessions=0,
                    total_episodes=0,
                    self_description=None,
                    core_values=settings.CME_IDENTITY_DEFAULT_VALUES,
                    is_enabled=settings.CME_IDENTITY_AUTO_ENABLE,
                )
                db.add(identity)

            print(f"  [OK] Creado: {u['email']} / {u['password']}  (rol: {u['role']}, área: {u['area_name']})")

        await db.commit()
        print("\nSeed completado.")


if __name__ == "__main__":
    asyncio.run(seed())
