"""
Analytics por usuario — KPIs en tiempo real.
Se actualiza después de cada mensaje.
"""
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import UserAnalytics, ChatSession, ChatMessage

CLARIFICATION_SIGNALS = [
    "¿puedes darme más contexto",
    "¿podrías especificar",
    "necesito más información",
    "¿a qué te refieres",
    "¿qué proceso específico",
    "¿cuál es el área",
]

REPHRASED_SIGNALS = [
    "no entendí", "no entendiste", "eso no es lo que", "me refería a",
    "te pregunté", "ya te dije", "repite", "otra vez",
]

NEGATIVE_SIGNALS = [
    "no sirve", "no funciona", "no entiendo", "no me ayuda", "inútil",
    "mal", "pésimo", "horrible", "no es lo que", "otra vez lo mismo",
    "no me entiende", "qué mal", "frustrado", "no tiene sentido",
]

POSITIVE_SIGNALS = [
    "gracias", "perfecto", "excelente", "muy bien", "genial", "útil",
    "me ayudó", "entendí", "claro", "exacto", "eso es", "bien",
]

FRUSTRATION_SIGNALS = [
    "no sirve", "inútil", "qué mal", "frustrado", "no me entiende",
    "horrible", "pésimo", "no tiene sentido", "otra vez lo mismo",
]

TOPIC_KEYWORDS = {
    "reportes": ["reporte", "informe", "dashboard", "métricas", "kpi"],
    "procesos": ["proceso", "flujo", "procedimiento", "workflow", "paso"],
    "equipo": ["equipo", "compañero", "colega", "área", "departamento"],
    "productividad": ["productividad", "eficiencia", "rendimiento", "desempeño"],
    "comunicación": ["comunicación", "reunión", "correo", "mensaje", "notificación"],
    "datos": ["dato", "base de datos", "información", "registro", "análisis"],
    "problema": ["problema", "error", "fallo", "issue", "bug", "no funciona"],
    "ayuda": ["ayuda", "cómo", "qué hago", "no sé", "explica"],
}


def extract_topics(text: str) -> list[str]:
    text_lower = text.lower()
    return [topic for topic, keywords in TOPIC_KEYWORDS.items()
            if any(kw in text_lower for kw in keywords)]


async def update_user_analytics(
    user_id: str,
    tenant_id: str | None,
    area_id: str | None,
    user_message: str,
    bot_response: str,
    session_id: str,
    db: AsyncSession
) -> None:
    """Actualiza los KPIs del usuario después de cada mensaje."""
    now = datetime.now(timezone.utc)

    # Obtener o crear registro de analytics
    result = await db.execute(select(UserAnalytics).where(UserAnalytics.user_id == user_id))
    ua = result.scalar_one_or_none()

    if not ua:
        ua = UserAnalytics(
            user_id=user_id,
            tenant_id=tenant_id,
            area_id=area_id,
            first_active=now,
        )
        db.add(ua)

    # Contar sesiones únicas
    sessions_q = await db.execute(
        select(func.count(ChatSession.id.distinct())).where(ChatSession.user_id == user_id)
    )
    ua.total_sessions = sessions_q.scalar() or 0

    # Actualizar contadores
    ua.total_messages += 1

    # Longitud promedio de mensajes
    ua.avg_message_length = (
        (ua.avg_message_length * (ua.total_messages - 1) + len(user_message))
        / ua.total_messages
    )

    # Detectar si el bot pidió clarificación
    bot_lower = bot_response.lower()
    if any(s in bot_lower for s in CLARIFICATION_SIGNALS):
        ua.clarification_requests += 1

    # Detectar si el usuario repreguntó
    msg_lower = user_message.lower()
    if any(s in msg_lower for s in REPHRASED_SIGNALS):
        ua.rephrased_questions += 1

    # Sentiment analysis
    if any(s in msg_lower for s in FRUSTRATION_SIGNALS):
        ua.frustration_alerts += 1
    if any(s in msg_lower for s in NEGATIVE_SIGNALS):
        ua.negative_sentiment_count += 1
    if any(s in msg_lower for s in POSITIVE_SIGNALS):
        ua.positive_sentiment_count += 1

    # Conversation quality score (0-100)
    total_fb = ua.thumbs_up + ua.thumbs_down
    fb_score = (ua.thumbs_up / total_fb * 60) if total_fb > 0 else 30
    neg_penalty = min(20, ua.negative_sentiment_count * 2)
    pos_bonus = min(20, ua.positive_sentiment_count * 2)
    ua.conversation_quality_score = round(max(0, min(100, fb_score + pos_bonus - neg_penalty)), 1)

    # Actualizar temas frecuentes
    topics = extract_topics(user_message)
    try:
        freq = json.loads(ua.topic_frequency or "{}")
    except Exception:
        freq = {}
    for topic in topics:
        freq[topic] = freq.get(topic, 0) + 1
    ua.topic_frequency = json.dumps(freq)

    # Actividad temporal
    ua.last_active = now
    if ua.first_active is None:
        ua.first_active = now

    # Días activos únicos (aproximado)
    if ua.last_active and ua.first_active:
        first = ua.first_active
        last = ua.last_active
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        delta = (last - first).days
        ua.active_days = max(1, delta + 1)

    await db.commit()


async def get_user_kpis(user_id: str, db: AsyncSession) -> dict:
    """Retorna los KPIs de un usuario."""
    result = await db.execute(select(UserAnalytics).where(UserAnalytics.user_id == user_id))
    ua = result.scalar_one_or_none()
    if not ua:
        return {"user_id": user_id, "no_data": True}

    try:
        topics = json.loads(ua.topic_frequency or "{}")
        top_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:5]
    except Exception:
        top_topics = []

    return {
        "user_id": user_id,
        "total_messages": ua.total_messages,
        "total_sessions": ua.total_sessions,
        "avg_message_length": round(ua.avg_message_length, 1),
        "clarification_requests": ua.clarification_requests,
        "rephrased_questions": ua.rephrased_questions,
        "top_topics": [{"topic": t, "count": c} for t, c in top_topics],
        "last_active": str(ua.last_active) if ua.last_active else None,
        "active_days": ua.active_days,
        "engagement_score": round(
            min(100, (ua.total_messages * 2 + ua.active_days * 5) / 10), 1
        ),
    }


async def get_area_kpis(area_id: str, tenant_id: str, db: AsyncSession) -> dict:
    """KPIs agregados del área — sin exponer datos individuales."""
    # Usuarios activos en el área
    users_q = await db.execute(
        select(func.count()).where(
            UserAnalytics.area_id == area_id
        )
    )
    active_users = users_q.scalar() or 0

    # Total mensajes del área
    msgs_q = await db.execute(
        select(func.count(ChatMessage.id))
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.area_id == area_id)
    )
    total_msgs = msgs_q.scalar() or 0

    # Temas agregados del área
    uas_q = await db.execute(
        select(UserAnalytics.topic_frequency).where(UserAnalytics.area_id == area_id)
    )
    area_topics: dict = {}
    for (tf,) in uas_q.fetchall():
        try:
            for topic, count in json.loads(tf or "{}").items():
                area_topics[topic] = area_topics.get(topic, 0) + count
        except Exception:
            pass
    top_area_topics = sorted(area_topics.items(), key=lambda x: x[1], reverse=True)[:5]

    # Promedio de clarificaciones (señal de calidad del bot)
    clarity_q = await db.execute(
        select(func.avg(UserAnalytics.clarification_requests))
        .where(UserAnalytics.area_id == area_id)
    )
    avg_clarity = round(clarity_q.scalar() or 0, 2)

    return {
        "area_id": area_id,
        "active_users": active_users,
        "total_messages": total_msgs,
        "top_topics": [{"topic": t, "count": c} for t, c in top_area_topics],
        "avg_clarification_requests": avg_clarity,
    }
