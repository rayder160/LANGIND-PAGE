"""
billing.py — Billing, uso y cuenta para ProxDeep
Endpoints:
  GET /api/billing/summary     → resumen del plan y uso actual
  GET /api/billing/history     → historial de pagos
  GET /api/billing/usage       → consumo por área
  GET /api/billing/alerts      → alertas de sobreuso
  GET /api/activity/recent     → actividad reciente de la organización
  GET /api/activity/alerts     → alertas detectadas por IA
  GET /api/activity/roi        → métricas de ROI de la IA

Datos: mock estructurado listo para conectar con Stripe/backend real.
TODO BACKEND: conectar con sistema de pagos real y métricas de uso reales.
"""

from fastapi import APIRouter, Depends
from app.routes.auth import get_current_user, require_admin
from app.models import User
from datetime import datetime, date

router = APIRouter(tags=["billing"])

# ── Mock de billing ──────────────────────────────────────────────────────────
BILLING_MOCK = {
    "plan": {
        "name": "Business",
        "status": "active",
        "renewal_date": "2026-06-21",
        "price_monthly": 890,
        "currency": "USD",
        "billing_cycle": "monthly",
        "seats_contracted": 25,
        "seats_used": 18,
        "models_allowed": ["gemma3:4b", "llama3.2:3b", "mistral:7b"],
        "default_model": "gemma3:4b",
        "features": ["IA Organizacional", "Workspace", "VS Code Extension", "API REST", "Soporte prioritario"],
    },
    "current_month": {
        "spend": 623,
        "limit": 890,
        "percent_used": 70,
        "messages_sent": 4820,
        "tokens_used": 1_240_000,
        "tokens_limit": 2_000_000,
        "estimated_eom": 812,
        "recommendation": None,
    },
    "usage_by_area": [
        {"area": "RRHH",        "messages": 1240, "tokens": 320000, "percent": 26},
        {"area": "Tecnología",  "messages": 980,  "tokens": 280000, "percent": 23},
        {"area": "Ventas",      "messages": 860,  "tokens": 220000, "percent": 18},
        {"area": "Operaciones", "messages": 740,  "tokens": 190000, "percent": 15},
        {"area": "Soporte",     "messages": 620,  "tokens": 150000, "percent": 12},
        {"area": "Finanzas",    "messages": 380,  "tokens": 80000,  "percent": 6},
    ],
    "payment_history": [
        {"date": "2026-05-01", "amount": 890, "status": "paid",    "invoice": "INV-2026-05"},
        {"date": "2026-04-01", "amount": 890, "status": "paid",    "invoice": "INV-2026-04"},
        {"date": "2026-03-01", "amount": 890, "status": "paid",    "invoice": "INV-2026-03"},
        {"date": "2026-02-01", "amount": 890, "status": "paid",    "invoice": "INV-2026-02"},
        {"date": "2026-01-01", "amount": 650, "status": "paid",    "invoice": "INV-2026-01"},
    ],
    "upcoming_invoice": {
        "date": "2026-06-01",
        "amount": 890,
        "status": "scheduled",
    },
    "alerts": [],
}

# Calcular recomendación y alertas dinámicamente
_pct = BILLING_MOCK["current_month"]["percent_used"]
if _pct >= 90:
    BILLING_MOCK["current_month"]["recommendation"] = "upgrade"
    BILLING_MOCK["alerts"].append({
        "type": "warning",
        "message": f"Consumiste el {_pct}% del límite mensual. Considera hacer upgrade.",
        "action": "Ver planes",
    })
elif _pct < 40:
    BILLING_MOCK["current_month"]["recommendation"] = "downgrade"
elif _pct >= 75:
    BILLING_MOCK["alerts"].append({
        "type": "info",
        "message": f"Vas al {_pct}% del límite. Estimación de fin de mes: ${BILLING_MOCK['current_month']['estimated_eom']}.",
    })

# ── Mock de actividad y alertas ──────────────────────────────────────────────
ACTIVITY_MOCK = [
    {"time": "hace 5 min",  "area": "Soporte",     "event": "56 tickets abiertos — récord del mes",          "type": "alert"},
    {"time": "hace 12 min", "area": "Tecnología",  "event": "Proyecto Incidencias CORE al 80% de avance",    "type": "progress"},
    {"time": "hace 28 min", "area": "Ventas",      "event": "Pipeline cayó 12% vs semana pasada",            "type": "warning"},
    {"time": "hace 1 h",    "area": "RRHH",        "event": "Onboarding IA: 3 nuevos empleados procesados",  "type": "success"},
    {"time": "hace 2 h",    "area": "Operaciones", "event": "Cuello de botella detectado en validación QA",  "type": "alert"},
    {"time": "hace 3 h",    "area": "Finanzas",    "event": "Presupuesto Q2 aprobado por Gerencia",          "type": "success"},
    {"time": "hace 4 h",    "area": "Tecnología",  "event": "Deploy de infraestructura base completado",     "type": "success"},
]

AI_ALERTS_MOCK = [
    {
        "id": "alert-1",
        "severity": "critical",
        "title": "Cuello de botella: Ventas → Operaciones",
        "description": "El pipeline de Ventas está generando órdenes más rápido de lo que Operaciones puede procesar. 34 tickets pendientes de validación.",
        "areas": ["ventas", "operaciones"],
        "project": "Automatización de Pedidos",
        "recommendation": "Revisar capacidad de Operaciones o pausar campañas de Ventas hasta resolver el backlog.",
        "detected_at": "2026-05-21T09:15:00",
    },
    {
        "id": "alert-2",
        "severity": "high",
        "title": "Documentación desactualizada en Tecnología",
        "description": "3 documentos clave del repositorio CORE no se actualizan hace más de 60 días. Esto genera consultas repetitivas al equipo.",
        "areas": ["tecnologia"],
        "project": None,
        "recommendation": "Asignar responsable de documentación técnica. Prioridad: Runbook de Incidencias.",
        "detected_at": "2026-05-21T08:30:00",
    },
    {
        "id": "alert-3",
        "severity": "medium",
        "title": "Baja adopción en Ventas",
        "description": "El área de Ventas tiene la tasa de resolución automática más baja (65%). Los usuarios no están usando el asistente para consultas de pipeline.",
        "areas": ["ventas"],
        "project": None,
        "recommendation": "Capacitación de 30 min sobre casos de uso del asistente para el equipo de Ventas.",
        "detected_at": "2026-05-21T07:45:00",
    },
]

ROI_MOCK = {
    "hours_saved_month": 1420,
    "tickets_avoided": 3850,
    "auto_resolution_rate": 0.82,
    "estimated_cost_saved_usd": 28400,
    "roi_multiplier": 31.9,
    "top_automation": "Respuestas automáticas de RRHH (onboarding y vacaciones)",
    "areas_by_adoption": [
        {"area": "RRHH",        "score": 92},
        {"area": "Soporte",     "score": 88},
        {"area": "Tecnología",  "score": 76},
        {"area": "Operaciones", "score": 71},
        {"area": "Finanzas",    "score": 65},
        {"area": "Ventas",      "score": 58},
    ],
    "ai_maturity_score": 74,
    "ai_maturity_label": "Avanzado",
}


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/billing/summary")
async def billing_summary(user: User = Depends(get_current_user)):
    """Resumen completo del plan, uso y estado de billing."""
    if user.role not in ("superadmin", "ceo", "admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Acceso restringido a administradores")
    return BILLING_MOCK


@router.get("/billing/history")
async def billing_history(user: User = Depends(get_current_user)):
    """Historial de pagos."""
    if user.role not in ("superadmin", "ceo", "admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Acceso restringido")
    return {
        "history": BILLING_MOCK["payment_history"],
        "upcoming": BILLING_MOCK["upcoming_invoice"],
    }


@router.get("/billing/usage")
async def billing_usage(user: User = Depends(get_current_user)):
    """Consumo por área."""
    if user.role not in ("superadmin", "ceo", "admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Acceso restringido")
    return {
        "by_area": BILLING_MOCK["usage_by_area"],
        "total_messages": BILLING_MOCK["current_month"]["messages_sent"],
        "total_tokens": BILLING_MOCK["current_month"]["tokens_used"],
    }


@router.get("/activity/recent")
async def recent_activity(_: User = Depends(get_current_user)):
    """Actividad reciente de la organización."""
    return ACTIVITY_MOCK


@router.get("/activity/alerts")
async def ai_alerts(_: User = Depends(get_current_user)):
    """Alertas detectadas por la IA."""
    return AI_ALERTS_MOCK


@router.get("/activity/roi")
async def roi_metrics(_: User = Depends(get_current_user)):
    """Métricas de ROI e impacto de la IA."""
    return ROI_MOCK
