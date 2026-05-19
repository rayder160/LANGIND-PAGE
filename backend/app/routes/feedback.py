from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models import MessageFeedback, ChatMessage, ChatSession, User, UserAnalytics
from app.routes.auth import get_current_user
from app.rag import store_chunk

# CME — feedback explícito actualiza quality_score de episodios (Req 6.2, 6.3)
try:
    from app.models.cme import AreaEpisode, AreaPattern
    import json as _json
    CME_FEEDBACK_AVAILABLE = True
except ImportError:
    CME_FEEDBACK_AVAILABLE = False

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    message_id: str
    rating: int  # 1 = 👍, -1 = 👎


@router.post("")
async def submit_feedback(data: FeedbackRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if data.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="Rating debe ser 1 o -1")

    # Verificar que el mensaje existe y pertenece al usuario
    msg_q = await db.execute(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatMessage.id == data.message_id, ChatSession.user_id == user.id)
    )
    message = msg_q.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado")

    # Evitar feedback duplicado
    existing = await db.execute(
        select(MessageFeedback).where(
            MessageFeedback.message_id == data.message_id,
            MessageFeedback.user_id == user.id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya enviaste feedback para este mensaje")

    # Guardar feedback
    fb = MessageFeedback(
        message_id=data.message_id,
        session_id=message.session_id,
        user_id=user.id,
        area_id=user.area_id,
        tenant_id=user.tenant_id,
        rating=data.rating,
    )
    db.add(fb)

    # Actualizar thumbs en analytics del usuario
    ua_q = await db.execute(select(UserAnalytics).where(UserAnalytics.user_id == user.id))
    ua = ua_q.scalar_one_or_none()
    if ua:
        if data.rating == 1:
            ua.thumbs_up += 1
        else:
            ua.thumbs_down += 1

    # Si es 👍 y el área tiene contexto, indexar como chunk de calidad en RAG
    if data.rating == 1 and user.area_id and len(message.content) > 30:
        # Buscar el mensaje del usuario que precedió esta respuesta
        session_msgs = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == message.session_id)
            .order_by(ChatMessage.created_at)
        )
        all_msgs = session_msgs.scalars().all()
        # Encontrar el mensaje de usuario anterior a esta respuesta
        for i, m in enumerate(all_msgs):
            if m.id == data.message_id and i > 0:
                prev = all_msgs[i - 1]
                if prev.role == "user":
                    content = f"Pregunta: {prev.content}\nRespuesta validada: {message.content}"
                    await store_chunk(user.area_id, content, "validated", db)
                break

    await db.commit()

    # CME — actualizar quality_score del episodio asociado a la sesión (Req 6.2, 6.3)
    if CME_FEEDBACK_AVAILABLE and user.area_id:
        try:
            # Buscar el episodio asociado a esta sesión
            episode_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.session_id == message.session_id,
                    AreaEpisode.area_id == user.area_id,
                    AreaEpisode.extraction_status == "completed"
                )
                .order_by(AreaEpisode.created_at.desc())
                .limit(1)
            )
            episode = episode_q.scalar_one_or_none()

            if episode:
                if data.rating == 1:
                    # 👍 — incrementar quality_score en 0.1 (cap 1.0) — Req 6.2
                    current = episode.quality_score or 0.5
                    episode.quality_score = round(min(1.0, current + 0.1), 4)

                    # Re-evaluar methodology promotion si quality >= 0.75 y arc = resolved
                    if episode.quality_score >= 0.75 and episode.session_arc == "resolved":
                        from app.cme.session_processor import _promote_methodology
                        await _promote_methodology(episode, user.area_id, user.tenant_id, db)

                elif data.rating == -1:
                    # 👎 — decrementar quality_score en 0.1 (min 0.0) — Req 6.3
                    current = episode.quality_score or 0.5
                    episode.quality_score = round(max(0.0, current - 0.1), 4)

                    # Reducir confidence_score de patrones que incluyen este episodio — Req 6.3
                    patterns_q = await db.execute(
                        select(AreaPattern)
                        .where(AreaPattern.area_id == user.area_id)
                    )
                    for pattern in patterns_q.scalars().all():
                        try:
                            source_ids = _json.loads(pattern.source_episode_ids or "[]")
                            if episode.id in source_ids:
                                pattern.confidence_score = round(
                                    max(0.0, pattern.confidence_score - 0.1), 4
                                )
                        except Exception:
                            pass

                await db.commit()
            else:
                # Req 6.5 — loguear warning si no se encuentra el episodio
                import logging
                logging.getLogger(__name__).warning(
                    f"CME feedback: no se encontró episodio para sesión {message.session_id}"
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"CME feedback: error actualizando episodio: {e}")

    return {"ok": True}
