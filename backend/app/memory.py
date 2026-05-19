"""
Memoria organizacional del área — 3 capas:
- Corto plazo: sesión actual (manejada en chat.py)
- Medio plazo: resumen de las últimas 2 semanas
- Largo plazo: conocimiento permanente (documentos + episodios validados)
"""
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import Area, ChatSession, ChatMessage, AreaChunk
from app.config import settings

# Triggers de actualización
SHORT_TERM_TRIGGER = 10    # regenerar resumen de medio plazo cada 10 mensajes
LONG_TERM_TRIGGER = 50     # regenerar conocimiento permanente cada 50 mensajes

# Ventanas de tiempo
MEDIUM_TERM_DAYS = 14      # últimas 2 semanas para medio plazo

MEMORY_PROMPT_MEDIUM = """Analiza estas conversaciones recientes y genera un resumen breve de:
- Temas o dudas frecuentes de las últimas semanas
- Soluciones que funcionaron
- Patrones recurrentes en las conversaciones

Máximo 150 palabras. Español. Sin listas, párrafo directo."""

MEMORY_PROMPT_LONG = """Analiza este historial de conversaciones y genera un perfil de conocimiento:
- Qué temas se tratan principalmente
- Terminología y conceptos específicos que aparecen
- Problemas recurrentes y cómo se resuelven
- Patrones importantes detectados

Máximo 200 palabras. Español. Sin listas, párrafo directo."""


async def get_area_context(area_id: str, db: AsyncSession) -> str | None:
    """
    Construye el contexto del área para enriquecer el system prompt.
    NO redefine la identidad del agente — solo aporta contexto organizacional.
    """
    result = await db.execute(select(Area).where(Area.id == area_id))
    area = result.scalar_one_or_none()
    if not area:
        return None

    parts = []

    # Largo plazo — conocimiento permanente
    if area.memory:
        parts.append(f"Conocimiento del área '{area.name}': {area.memory}")

    # Medio plazo — actividad reciente
    if area.memory_recent:
        parts.append(f"Actividad reciente: {area.memory_recent}")

    if not parts:
        return None  # Sin contexto → el LLM usa solo su identidad base

    return "\n\n".join(parts)


async def maybe_update_memory(area_id: str, db: AsyncSession) -> None:
    """Verifica si hay que actualizar alguna capa de memoria."""
    count_q = await db.execute(
        select(func.count(ChatMessage.id))
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.area_id == area_id, ChatMessage.role == "user")
    )
    total = count_q.scalar() or 0

    if total > 0 and total % SHORT_TERM_TRIGGER == 0:
        await _update_medium_term(area_id, db)

    if total > 0 and total % LONG_TERM_TRIGGER == 0:
        await _update_long_term(area_id, db)


async def _update_medium_term(area_id: str, db: AsyncSession) -> None:
    """Actualiza el resumen de medio plazo (últimas 2 semanas)."""
    area_q = await db.execute(select(Area).where(Area.id == area_id))
    area = area_q.scalar_one_or_none()
    if not area:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=MEDIUM_TERM_DAYS)

    msgs_q = await db.execute(
        select(ChatMessage.role, ChatMessage.content)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatSession.area_id == area_id,
            ChatMessage.created_at >= cutoff
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(60)
    )
    rows = msgs_q.fetchall()
    if len(rows) < 3:
        return

    sample = "\n".join(
        f"{'Usuario' if r == 'user' else 'Bot'}: {c[:120]}"
        for r, c in reversed(rows[:40])
    )

    memory = await _call_llm_for_summary(
        f"{MEMORY_PROMPT_MEDIUM}\n\nConversaciones recientes en '{area.name}':\n{sample}"
    )
    if memory:
        area.memory_recent = memory.strip()
        area.memory_updated_at = datetime.now(timezone.utc)
        await db.commit()


async def _update_long_term(area_id: str, db: AsyncSession) -> None:
    """Actualiza el conocimiento permanente del área."""
    area_q = await db.execute(select(Area).where(Area.id == area_id))
    area = area_q.scalar_one_or_none()
    if not area:
        return

    # Tomar muestra representativa de toda la historia
    msgs_q = await db.execute(
        select(ChatMessage.role, ChatMessage.content)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.area_id == area_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(100)
    )
    rows = msgs_q.fetchall()
    if len(rows) < 10:
        return

    # También incluir chunks validados (👍) como fuente de verdad
    validated_q = await db.execute(
        select(AreaChunk.content)
        .where(AreaChunk.area_id == area_id, AreaChunk.source == "validated")
        .limit(20)
    )
    validated = [row[0] for row in validated_q.fetchall()]

    sample = "\n".join(
        f"{'Usuario' if r == 'user' else 'Bot'}: {c[:120]}"
        for r, c in reversed(rows[:60])
    )

    extra = ""
    if validated:
        extra = "\n\nRespuestas validadas:\n" + "\n".join(v[:200] for v in validated[:10])

    memory = await _call_llm_for_summary(
        f"{MEMORY_PROMPT_LONG}\n\nHistorial de '{area.name}':\n{sample}{extra}"
    )
    if memory:
        area.memory = memory.strip()
        await db.commit()


async def _call_llm_for_summary(prompt: str) -> str | None:
    """Llama al LLM para generar un resumen."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                settings.LLM_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.LLM_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": settings.LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            if res.status_code == 200:
                return res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception:
        pass
    return None
