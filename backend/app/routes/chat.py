from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel
from app.database import get_db
from app.models import User, ChatSession, ChatMessage
from app.routes.auth import get_current_user
from app.llm import query_llm
from app.memory import get_area_context, maybe_update_memory
from app.rag import search_relevant, index_conversation
from app.analytics import update_user_analytics
from app.advanced_analytics import log_activity

# CME — Cognitive Memory Engine (backward-compatible: falla silenciosamente si no está disponible)
try:
    from app.cme.working_memory import working_memory as cme_working_memory
    from app.cme.context_enricher import context_enricher
    from app.cme.session_processor import process_session_signals
    from app.database import AsyncSessionLocal
    CME_AVAILABLE = True
except ImportError:
    CME_AVAILABLE = False

router = APIRouter(prefix="/chat", tags=["chat"])

CLARIFICATION_RESPONSE = "Tu pregunta es un poco amplia. ¿Puedes darme más contexto? Por ejemplo: ¿qué proceso específico, qué área o qué situación tienes en mente?"


class MessageRequest(BaseModel):
    session_id: str | None = None
    content: str


class SessionCreate(BaseModel):
    title: str = "Nueva conversación"


@router.get("/sessions")
async def get_sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatSession).where(ChatSession.user_id == user.id).order_by(desc(ChatSession.updated_at))
    )
    sessions = result.scalars().all()
    return [{"id": s.id, "title": s.title, "created_at": str(s.created_at), "updated_at": str(s.updated_at)} for s in sessions]


@router.post("/sessions")
async def create_session(data: SessionCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session = ChatSession(user_id=user.id, tenant_id=user.tenant_id, area_id=user.area_id, title=data.title)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {"id": session.id, "title": session.title}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    await db.delete(session)
    await db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    msgs = await db.execute(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at))
    return [{"role": m.role, "content": m.content, "created_at": str(m.created_at)} for m in msgs.scalars().all()]


@router.post("/message")
async def send_message(data: MessageRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Crear o recuperar sesión
    if not data.session_id:
        session = ChatSession(user_id=user.id, tenant_id=user.tenant_id, area_id=user.area_id, title=data.content[:50])
        db.add(session)
        await db.commit()
        await db.refresh(session)
        session_id = session.id
    else:
        result = await db.execute(select(ChatSession).where(ChatSession.id == data.session_id, ChatSession.user_id == user.id))
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        session_id = data.session_id

    # Guardar mensaje del usuario
    user_msg = ChatMessage(session_id=session_id, role="user", content=data.content)
    db.add(user_msg)
    await db.commit()

    # Respuestas directas sin LLM para preguntas sobre el contexto del usuario
    content_lower = data.content.lower()
    if any(p in content_lower for p in ["en qué área", "en que area", "qué área soy", "mi área", "a qué área", "que area estoy"]):
        if user.area_id:
            from app.models import Area
            area_q = await db.execute(select(Area).where(Area.id == user.area_id))
            area = area_q.scalar_one_or_none()
            area_name = area.name if area else "sin área asignada"
        else:
            area_name = "sin área asignada"
        direct = f"Estás en el área de {area_name}."
        ai_msg = ChatMessage(session_id=session_id, role="assistant", content=direct)
        db.add(ai_msg)
        await db.commit()
        return {"session_id": session_id, "response": direct}

    # Obtener historial reciente (gestión inteligente de tokens)
    msgs_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    all_msgs = msgs_result.scalars().all()
    history = [{"role": m.role, "content": m.content} for m in all_msgs]

    # Construir system prompt con contexto del área
    area_context = None
    if user.area_id:
        area_context = await get_area_context(user.area_id, db)

    # RAG — buscar chunks relevantes solo si la pregunta tiene sustancia
    if user.area_id and len(data.content.split()) > 3:
        relevant_chunks = await search_relevant(user.area_id, data.content, db)
        if relevant_chunks and area_context:
            rag_context = "\n\n".join(relevant_chunks)
            area_context += f"\n\nInformación relevante del equipo:\n{rag_context}"

    # CME — enriquecer system prompt con memoria cognitiva (fail-silent)
    if CME_AVAILABLE and user.area_id and len(data.content.split()) > 3:
        try:
            await cme_working_memory.update_topic(session_id, data.content)
            await cme_working_memory.update_emotion(session_id, data.content)
            wm_ctx = await cme_working_memory.get_or_create(session_id)
            cme_payload = await context_enricher.enrich(
                query=data.content,
                area_id=user.area_id,
                tenant_id=user.tenant_id,
                working_memory=wm_ctx,
                db=db,
                user_id=user.id
            )
            if cme_payload and area_context:
                area_context += f"\n\n{cme_payload}"
            elif cme_payload:
                area_context = cme_payload
        except Exception:
            pass  # fail-silent: continúa sin CME

    # Combinar identidad base de IM con contexto del área
    from app.llm import SYSTEM_PROMPT as IM_IDENTITY
    if area_context:
        combined_system = f"{IM_IDENTITY}\n\n---\n{area_context}"
    else:
        combined_system = None  # query_llm usa SYSTEM_PROMPT por defecto

    response = await query_llm(history, system_context=combined_system)

    # CME — si el LLM falló, intentar responder desde la memoria cognitiva
    LLM_ERROR_SIGNALS = ("Estoy con mucha demanda", "Tardé demasiado", "Error de conexión", "No pude procesar")
    if CME_AVAILABLE and user.area_id and any(s in response for s in LLM_ERROR_SIGNALS):
        try:
            wm_ctx = await cme_working_memory.get_or_create(session_id)
            memory_response = await context_enricher.respond_from_memory(
                query=data.content,
                area_id=user.area_id,
                tenant_id=user.tenant_id,
                working_memory=wm_ctx,
                db=db,
                user_id=user.id
            )
            if memory_response:
                response = memory_response
        except Exception:
            pass  # fail-silent: mantiene el mensaje de error original

    # Guardar respuesta
    ai_msg = ChatMessage(session_id=session_id, role="assistant", content=response)
    db.add(ai_msg)

    if len(history) == 1:
        session.title = data.content[:60]

    await db.commit()

    # CME — registrar background task de procesamiento de señales de sesión
    if CME_AVAILABLE and user.area_id and user.tenant_id and len(data.content.split()) > 3:
        try:
            import asyncio
            asyncio.create_task(process_session_signals(
                session_id=session_id,
                area_id=user.area_id,
                tenant_id=user.tenant_id,
                user_id=user.id,
                db_factory=AsyncSessionLocal
            ))
        except Exception:
            pass  # fail-silent

    # Post-proceso: indexar para RAG + actualizar memoria + analytics
    if user.area_id and len(data.content.split()) > 3:
        await index_conversation(user.area_id, data.content, response, db)
        await maybe_update_memory(user.area_id, db)

    await update_user_analytics(
        user_id=user.id,
        tenant_id=user.tenant_id,
        area_id=user.area_id,
        user_message=data.content,
        bot_response=response,
        session_id=session_id,
        db=db,
    )

    # Log de actividad para heatmap
    if user.area_id and user.tenant_id:
        await log_activity(user.area_id, user.tenant_id, db)

    return {"session_id": session_id, "response": response, "message_id": ai_msg.id}
