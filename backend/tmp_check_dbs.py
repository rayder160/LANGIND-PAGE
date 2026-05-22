import asyncio
import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from app.models import User
from app.database import Base

async def check_db(path):
    path = Path(path).resolve()
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    engine = create_async_engine(url, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == 'empleado@proxdeep.com'))
        user = result.scalar_one_or_none()
        print('DB', path, 'found', bool(user))
        if user:
            print('  email', user.email)
            print('  role', user.role)
            print('  active', user.is_active)
            print('  pass', user.hashed_password[:60])
    await engine.dispose()

async def main():
    cwd = Path('.').resolve()
    print('CWD', cwd)
    await check_db(cwd / 'proxdeep.db')
    await check_db(cwd / 'backend' / 'proxdeep.db')

asyncio.run(main())
