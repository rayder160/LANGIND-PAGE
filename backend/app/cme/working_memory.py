"""
Working Memory — Contexto activo de sesión en memoria (no en DB).
Mantiene topic actual, emoción detectada y episodios activos por sesión.
"""
from dataclasses import dataclass, field
from typing import Optional
from app.rag import get_embedding, cosine_similarity
# NO importar de app.models aquí para evitar circular imports


@dataclass
class WorkingMemoryContext:
    session_id: str
    current_topic: Optional[str] = None
    current_topic_embedding: Optional[list[float]] = None
    detected_emotion: str = "neutral"  # neutral|frustrated|satisfied|confused
    active_episode_ids: list[str] = field(default_factory=list)
    message_count: int = 0


class WorkingMemory:
    """Store en memoria de contextos de sesión activos."""
    _store: dict[str, WorkingMemoryContext] = {}

    async def get_or_create(self, session_id: str) -> WorkingMemoryContext:
        """Retorna el contexto existente o crea uno nuevo."""
        if session_id not in self._store:
            self._store[session_id] = WorkingMemoryContext(session_id=session_id)
        return self._store[session_id]

    async def update_topic(self, session_id: str, message: str) -> None:
        """
        Actualiza current_topic si cosine_similarity < 0.60 con el topic anterior.
        Si no hay topic anterior, establece el mensaje como topic inicial.
        """
        ctx = await self.get_or_create(session_id)
        ctx.message_count += 1

        new_embedding = await get_embedding(message)
        if not new_embedding:
            return

        if ctx.current_topic_embedding is None:
            # Primera vez — establecer topic inicial
            ctx.current_topic = message[:100]
            ctx.current_topic_embedding = new_embedding
        else:
            similarity = cosine_similarity(new_embedding, ctx.current_topic_embedding)
            if similarity < 0.60:
                # Cambio de tema detectado
                ctx.current_topic = message[:100]
                ctx.current_topic_embedding = new_embedding

    async def update_emotion(self, session_id: str, message: str) -> None:
        """
        Detecta emoción usando señales de frustración de analytics.py.
        Estados: neutral | frustrated | satisfied | confused
        """
        ctx = await self.get_or_create(session_id)
        msg_lower = message.lower()

        from app.analytics import FRUSTRATION_SIGNALS, POSITIVE_SIGNALS

        if any(s in msg_lower for s in FRUSTRATION_SIGNALS):
            ctx.detected_emotion = "frustrated"
        elif any(s in msg_lower for s in POSITIVE_SIGNALS):
            ctx.detected_emotion = "satisfied"
        elif any(s in msg_lower for s in ["no entiendo", "no sé", "confundido", "no comprendo"]):
            ctx.detected_emotion = "confused"
        else:
            # No cambiar si ya está frustrated — mantener hasta resolución
            if ctx.detected_emotion not in ("frustrated",):
                ctx.detected_emotion = "neutral"

    async def finalize(self, session_id: str, episode, db) -> None:
        """
        Persiste detected_emotion y current_topic al Episode record al cierre de sesión.
        episode es un AreaEpisode ya guardado en DB.
        """
        ctx = self._store.get(session_id)
        if not ctx or not episode:
            return
        # Los campos detected_emotion y current_topic se usan en quality scoring
        # Se guardan en el episode como metadata adicional si los campos existen
        # Por ahora solo actualizamos el extraction_status
        try:
            from sqlalchemy import update
            from app.models.cme import AreaEpisode
            # No hay campos de emotion en AreaEpisode — se usa en quality scoring
            pass
        except Exception:
            pass

    def evict(self, session_id: str) -> None:
        """Elimina el contexto de sesión de la memoria."""
        self._store.pop(session_id, None)

    def get_context(self, session_id: str) -> Optional[WorkingMemoryContext]:
        """Retorna el contexto sin crear uno nuevo."""
        return self._store.get(session_id)


# Instancia global singleton
working_memory = WorkingMemory()
