"""
Control de suscripciones y pagos.
Maneja el ciclo de vida: trial → active → suspended → cancelled
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Tenant, User

# Días de gracia después de vencer antes de suspender
GRACE_PERIOD_DAYS = 3
# Días suspendido antes de cancelar automáticamente
CANCEL_AFTER_DAYS = 30


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def subscription_summary(tenant: Tenant) -> dict:
    now = now_utc()
    status = tenant.subscription_status
    days_left = None
    expires_label = None

    if status == "trial" and tenant.trial_ends_at:
        trial_end = tenant.trial_ends_at
        if trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)
        delta = trial_end - now
        days_left = max(0, delta.days)
        expires_label = trial_end.strftime("%d/%m/%Y")

    elif status == "active" and tenant.subscription_expires_at:
        exp = tenant.subscription_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        delta = exp - now
        days_left = max(0, delta.days)
        expires_label = exp.strftime("%d/%m/%Y")

    elif status == "suspended" and tenant.suspended_at:
        sus = tenant.suspended_at
        if sus.tzinfo is None:
            sus = sus.replace(tzinfo=timezone.utc)
        cancel_date = sus + timedelta(days=CANCEL_AFTER_DAYS)
        delta = cancel_date - now
        days_left = max(0, delta.days)
        expires_label = f"Cancela el {cancel_date.strftime('%d/%m/%Y')}"

    return {
        "subscription_status": status,
        "billing_cycle": tenant.billing_cycle,
        "days_left": days_left,
        "expires_at": expires_label,
        "trial_ends_at": tenant.trial_ends_at.strftime("%d/%m/%Y") if tenant.trial_ends_at else None,
        "subscription_expires_at": tenant.subscription_expires_at.strftime("%d/%m/%Y") if tenant.subscription_expires_at else None,
    }


async def check_and_update_status(tenant: Tenant, db: AsyncSession) -> str:
    """
    Verifica si el tenant debe cambiar de estado automáticamente.
    Retorna el nuevo status.
    """
    now = now_utc()
    status = tenant.subscription_status

    if status == "trial":
        trial_end = tenant.trial_ends_at
        if trial_end:
            if trial_end.tzinfo is None:
                trial_end = trial_end.replace(tzinfo=timezone.utc)
            if now > trial_end:
                tenant.subscription_status = "suspended"
                tenant.suspended_at = now
                tenant.is_active = False
                await db.commit()
                return "suspended"

    elif status == "active":
        exp = tenant.subscription_expires_at
        if exp:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            grace = exp + timedelta(days=GRACE_PERIOD_DAYS)
            if now > grace:
                tenant.subscription_status = "suspended"
                tenant.suspended_at = now
                tenant.is_active = False
                await db.commit()
                return "suspended"

    elif status == "suspended":
        sus = tenant.suspended_at
        if sus:
            if sus.tzinfo is None:
                sus = sus.replace(tzinfo=timezone.utc)
            if now > sus + timedelta(days=CANCEL_AFTER_DAYS):
                tenant.subscription_status = "cancelled"
                tenant.cancelled_at = now
                tenant.is_active = False
                await db.commit()
                return "cancelled"

    return status


async def activate_subscription(tenant: Tenant, billing_cycle: str, db: AsyncSession) -> dict:
    """
    Activa o renueva la suscripción de un tenant después de un pago.
    """
    now = now_utc()
    days = 365 if billing_cycle == "annual" else 30

    tenant.subscription_status = "active"
    tenant.billing_cycle = billing_cycle
    tenant.subscription_expires_at = now + timedelta(days=days)
    tenant.suspended_at = None
    tenant.is_active = True

    await db.commit()
    return subscription_summary(tenant)


async def suspend_tenant(tenant: Tenant, db: AsyncSession) -> None:
    """Suspende manualmente un tenant (no pago, violación, etc.)"""
    tenant.subscription_status = "suspended"
    tenant.suspended_at = now_utc()
    tenant.is_active = False
    await db.commit()


async def cancel_tenant(tenant: Tenant, db: AsyncSession) -> None:
    """Cancela definitivamente un tenant."""
    tenant.subscription_status = "cancelled"
    tenant.cancelled_at = now_utc()
    tenant.is_active = False
    await db.commit()
