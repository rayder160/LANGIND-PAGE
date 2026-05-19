"""
Analytics avanzados — Bloque 4:
- Heatmap de actividad (hora x día de semana)
- Gaps de conocimiento (temas sin respuesta buena)
- Comparativa entre áreas
"""
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.models import (
    Area, User, ChatSession, ChatMessage,
    UserAnalytics, AreaActivityLog, AreaChunk, MessageFeedback
)

WEEKDAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

# Señales de que el bot no pudo responder bien
GAP_SIGNALS = [
    "no tengo esa información",
    "no tengo información",
    "no puedo ayudarte con eso",
    "no tengo datos",
    "no sé",
]


async def log_activity(area_id: str, tenant_id: str, db: AsyncSession) -> None:
    """Registra actividad en el log de heatmap."""
    now = datetime.now(timezone.utc)
    hour = now.hour
    weekday = now.weekday()
    date_str = now.strftime("%Y-%m-%d")

    # Buscar si ya existe un registro para esta hora/día/área
    existing = await db.execute(
        select(AreaActivityLog).where(
            AreaActivityLog.area_id == area_id,
            AreaActivityLog.date == date_str,
            AreaActivityLog.hour == hour,
        )
    )
    log = existing.scalar_one_or_none()

    if log:
        log.message_count += 1
    else:
        log = AreaActivityLog(
            area_id=area_id,
            tenant_id=tenant_id,
            hour=hour,
            weekday=weekday,
            date=date_str,
        )
        db.add(log)

    await db.commit()


async def get_heatmap(area_id: str, db: AsyncSession) -> list[dict]:
    """
    Retorna datos para heatmap: actividad por hora y día de semana.
    Formato: [{weekday: 0, hour: 9, count: 15}, ...]
    """
    result = await db.execute(
        select(
            AreaActivityLog.weekday,
            AreaActivityLog.hour,
            func.sum(AreaActivityLog.message_count).label("total")
        )
        .where(AreaActivityLog.area_id == area_id)
        .group_by(AreaActivityLog.weekday, AreaActivityLog.hour)
    )
    rows = result.fetchall()
    return [
        {"weekday": r.weekday, "weekday_name": WEEKDAYS[r.weekday], "hour": r.hour, "count": r.total}
        for r in rows
    ]


async def get_knowledge_gaps(area_id: str, db: AsyncSession) -> list[dict]:
    """
    Detecta gaps de conocimiento: preguntas donde el bot no pudo responder bien.
    Señales: respuestas con "no tengo información" + mensajes con 👎 + repregunta del usuario.
    """
    gaps = []

    # 1. Respuestas del bot con señales de gap
    msgs_q = await db.execute(
        select(ChatMessage.content, ChatMessage.id, ChatMessage.session_id)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatSession.area_id == area_id,
            ChatMessage.role == "assistant"
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(200)
    )
    bot_msgs = msgs_q.fetchall()

    gap_sessions = set()
    for content, msg_id, session_id in bot_msgs:
        content_lower = content.lower()
        if any(sig in content_lower for sig in GAP_SIGNALS):
            gap_sessions.add(session_id)

    # 2. Para cada sesión con gap, encontrar la pregunta del usuario
    topic_gaps: dict[str, int] = {}
    for session_id in list(gap_sessions)[:20]:
        user_q = await db.execute(
            select(ChatMessage.content)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "user")
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        row = user_q.scalar_one_or_none()
        if row:
            # Extraer tema principal (primeras 5 palabras)
            topic = " ".join(row.split()[:5])
            topic_gaps[topic] = topic_gaps.get(topic, 0) + 1

    # 3. Mensajes con 👎
    thumbs_down_q = await db.execute(
        select(ChatMessage.content)
        .join(MessageFeedback, MessageFeedback.message_id == ChatMessage.id)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatSession.area_id == area_id,
            MessageFeedback.rating == -1
        )
        .limit(20)
    )
    for (content,) in thumbs_down_q.fetchall():
        topic = " ".join(content.split()[:5])
        topic_gaps[topic] = topic_gaps.get(topic, 0) + 2  # peso mayor para 👎

    gaps = [
        {"topic": topic, "frequency": freq, "severity": "alta" if freq >= 3 else "media" if freq >= 2 else "baja"}
        for topic, freq in sorted(topic_gaps.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    return gaps


async def get_area_comparison(tenant_id: str, db: AsyncSession) -> list[dict]:
    """
    Comparativa entre áreas del tenant:
    - Mensajes totales
    - Usuarios activos
    - Engagement promedio
    - Quality score promedio
    - Gaps detectados
    """
    areas_q = await db.execute(select(Area).where(Area.tenant_id == tenant_id))
    areas = areas_q.scalars().all()

    comparison = []
    for area in areas:
        # Mensajes del área
        msgs_q = await db.execute(
            select(func.count(ChatMessage.id))
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(ChatSession.area_id == area.id)
        )
        total_msgs = msgs_q.scalar() or 0

        # Usuarios activos
        users_q = await db.execute(
            select(func.count()).where(
                UserAnalytics.area_id == area.id
            )
        )
        active_users = users_q.scalar() or 0

        # Engagement y quality promedio
        avg_q = await db.execute(
            select(
                func.avg(UserAnalytics.engagement_score if hasattr(UserAnalytics, 'engagement_score') else 0),
                func.avg(UserAnalytics.conversation_quality_score),
                func.sum(UserAnalytics.frustration_alerts),
            ).where(UserAnalytics.area_id == area.id)
        )
        avg_row = avg_q.fetchone()

        # Chunks de conocimiento indexados
        chunks_q = await db.execute(
            select(func.count()).where(AreaChunk.area_id == area.id)
        )
        knowledge_chunks = chunks_q.scalar() or 0

        comparison.append({
            "area_id": area.id,
            "area_name": area.name,
            "total_messages": total_msgs,
            "active_users": active_users,
            "avg_quality_score": round(avg_row[1] or 0, 1),
            "total_frustration_alerts": int(avg_row[2] or 0),
            "knowledge_chunks": knowledge_chunks,
            "has_memory": bool(area.memory),
            "has_recent_memory": bool(area.memory_recent),
        })

    # Ordenar por mensajes totales
    comparison.sort(key=lambda x: x["total_messages"], reverse=True)
    return comparison
