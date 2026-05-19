from sqlalchemy.orm import DeclarativeBase
import uuid, secrets
from datetime import datetime, timezone, timedelta

class Base(DeclarativeBase):
    pass

def gen_id() -> str:
    return str(uuid.uuid4())

def gen_api_key() -> str:
    return "sk-org-" + secrets.token_urlsafe(32)

def trial_end() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=14)
