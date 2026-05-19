"""
Quality Signal Engine — Calcula Session_Quality_Score (0-1) usando señales implícitas y explícitas.
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import ChatSession, ChatMessage
from app.models.analytics import MessageFeedback
from app.analytics import FRUSTRATION_SIGNALS, POSITIVE_SIGNALS, REPHRASED_SIGNALS

RESOLUTION_SIGNALS = ["gracias", "perfecto", "entendí", "listo", "excelente", "resuelto", "claro", "exacto"]
DISENGAGEMENT_THRESHOLD_MINUTES = 3
LENGTH_TREND_THRESHOLD = 0.40  # últimos 3 < 40% de primeros 3


class QualitySignalEngine:

    async def compute_score(
        self,
        session_id: str,
        session_arc: str,
        db: AsyncSession
    ) -> float:
        """Calcula Session_Quality_Score en [0.0, 1.0]."""
        # Obtener mensajes de la sesión
        msgs_q = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        messages = msgs_q.scalars().all()

        if not messages:
            return 0.3  # score neutral por defecto

        # Componentes del score
        implicit_positive = self.compute_implicit_positive(messages)
        implicit_negative = self.compute_implicit_negative(messages)
        explicit_feedback = await self._get_explicit_feedback(session_id, db)
        resolution_score = self._get_resolution_score(session_arc)
        retention_score = await self._get_retention_score(session_id, db)

        score = (
            0.30 * implicit_positive +
            0.25 * (1.0 - implicit_negative) +
            0.20 * explicit_feedback +
            0.15 * resolution_score +
            0.10 * retention_score
        )

        return round(max(0.0, min(1.0, score)), 4)

    def compute_implicit_positive(self, messages: list) -> float:
        """Señales positivas implícitas (0-1)."""
        signals = 0
        total_possible = 4

        user_msgs = [m for m in messages if m.role == "user"]
        if not user_msgs:
            return 0.0

        # 1. Usuario NO reformuló la misma pregunta
        if not self._has_rephrasing(user_msgs):
            signals += 1

        # 2. Sesión terminó naturalmente (mensaje de cierre detectado)
        last_user_msg = user_msgs[-1].content.lower() if user_msgs else ""
        if any(s in last_user_msg for s in RESOLUTION_SIGNALS):
            signals += 1

        # 3. Sesión corta con resolución clara (≤ 6 mensajes de usuario y terminó bien)
        if len(user_msgs) <= 6 and any(s in last_user_msg for s in RESOLUTION_SIGNALS):
            signals += 1

        # 4. Sin señales de desenganche por velocidad
        disengagement_count = self.compute_message_velocity_signals(messages)
        if disengagement_count == 0:
            signals += 1

        return signals / total_possible

    def compute_implicit_negative(self, messages: list) -> float:
        """Señales negativas implícitas (0-1) — mayor = peor."""
        signals = 0
        total_possible = 4

        user_msgs = [m for m in messages if m.role == "user"]
        if not user_msgs:
            return 0.0

        # 1. Usuario reformuló la misma pregunta
        if self._has_rephrasing(user_msgs):
            signals += 1

        # 2. Frases de frustración
        all_user_text = " ".join(m.content.lower() for m in user_msgs)
        if any(s in all_user_text for s in FRUSTRATION_SIGNALS):
            signals += 1

        # 3. Tendencia de longitud decreciente
        if self.compute_length_trend_signal(messages):
            signals += 1

        # 4. Múltiples señales de desenganche
        if self.compute_message_velocity_signals(messages) >= 2:
            signals += 1

        return signals / total_possible

    def compute_message_velocity_signals(self, messages: list) -> int:
        """Cuenta intervalos > 3 min entre respuesta bot y siguiente mensaje usuario."""
        disengagement_count = 0
        threshold = timedelta(minutes=DISENGAGEMENT_THRESHOLD_MINUTES)

        for i in range(len(messages) - 1):
            current = messages[i]
            next_msg = messages[i + 1]

            if current.role == "assistant" and next_msg.role == "user":
                try:
                    t1 = current.created_at
                    t2 = next_msg.created_at
                    if t1.tzinfo is None:
                        t1 = t1.replace(tzinfo=timezone.utc)
                    if t2.tzinfo is None:
                        t2 = t2.replace(tzinfo=timezone.utc)
                    if (t2 - t1) > threshold:
                        disengagement_count += 1
                except Exception:
                    pass

        return disengagement_count

    def compute_length_trend_signal(self, messages: list) -> bool:
        """True si últimos 3 msgs de usuario < 40% de longitud promedio de primeros 3."""
        user_msgs = [m for m in messages if m.role == "user"]
        if len(user_msgs) < 6:
            return False

        first_3_avg = sum(len(m.content) for m in user_msgs[:3]) / 3
        last_3_avg = sum(len(m.content) for m in user_msgs[-3:]) / 3

        if first_3_avg == 0:
            return False

        return (last_3_avg / first_3_avg) < LENGTH_TREND_THRESHOLD

    def _has_rephrasing(self, user_msgs: list) -> bool:
        """Detecta si el usuario reformuló la misma pregunta."""
        all_text = " ".join(m.content.lower() for m in user_msgs)
        return any(s in all_text for s in REPHRASED_SIGNALS)

    def _get_resolution_score(self, session_arc: str) -> float:
        """Score de resolución según session_arc."""
        scores = {
            "resolved": 1.0,
            "neutral": 0.5,
            "degraded": 0.2,
            "abandoned": 0.0,
        }
        return scores.get(session_arc, 0.3)

    async def _get_explicit_feedback(self, session_id: str, db: AsyncSession) -> float:
        """Obtiene feedback explícito (thumbs up/down) de la sesión."""
        try:
            fb_q = await db.execute(
                select(MessageFeedback)
                .where(MessageFeedback.session_id == session_id)
                .order_by(MessageFeedback.created_at.desc())
                .limit(1)
            )
            fb = fb_q.scalar_one_or_none()
            if fb is None:
                return 0.5  # neutral
            return 1.0 if fb.rating > 0 else 0.0
        except Exception:
            return 0.5

    async def _get_retention_score(self, session_id: str, db: AsyncSession) -> float:
        """Retorna 1.0 si el usuario regresó dentro de 24h, 0.0 si no."""
        try:
            session_q = await db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            session = session_q.scalar_one_or_none()
            if not session:
                return 0.0

            # Buscar sesión posterior del mismo usuario dentro de 24h
            updated = session.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            cutoff = updated + timedelta(hours=24)

            next_session_q = await db.execute(
                select(ChatSession)
                .where(
                    ChatSession.user_id == session.user_id,
                    ChatSession.id != session_id,
                    ChatSession.created_at <= cutoff
                )
                .order_by(ChatSession.created_at.desc())
                .limit(1)
            )
            next_session = next_session_q.scalar_one_or_none()
            return 1.0 if next_session else 0.0
        except Exception:
            return 0.0


# Instancia global singleton
quality_signal_engine = QualitySignalEngine()
