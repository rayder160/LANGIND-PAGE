"""
Episode Extractor — Procesa sesiones completas y extrae episodios estructurados.
Ejecuta como background task, nunca bloquea la respuesta al usuario.
"""
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import ChatSession, ChatMessage
from app.models.cme import AreaEpisode
from app.rag import get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

RESOLUTION_SIGNALS = ["gracias", "perfecto", "entendí", "listo", "excelente", "resuelto", "claro", "exacto", "genial"]
FRUSTRATION_SIGNALS = ["no sirve", "inútil", "qué mal", "frustrado", "no me entiende", "horrible", "pésimo", "no tiene sentido", "otra vez lo mismo"]

EXTRACT_PROMPT = """Analiza esta conversación y extrae en JSON (solo JSON, sin texto adicional):
{{
  "situation": "descripción del problema planteado (máximo 400 caracteres)",
  "strategy": "estrategia o enfoque aplicado (máximo 400 caracteres)",
  "outcome": "resultado obtenido (máximo 300 caracteres)",
  "session_arc": "resolved|degraded|neutral|abandoned"
}}

Conversación:
{transcript}"""

EXTRACT_PROMPT_SIMPLE = """Extrae en JSON:
{{"situation": "...", "strategy": "...", "outcome": "...", "session_arc": "resolved|degraded|neutral|abandoned"}}

Conversación:
{transcript}"""

CAUSAL_PROMPT = """¿Por qué funcionó esta estrategia? Explica el mecanismo causal en 1-2 oraciones (máximo 300 caracteres)."""

FAILURE_PROMPT = """¿Qué salió mal y por qué? Explica en 1-2 oraciones (máximo 300 caracteres)."""


class EpisodeExtractor:

    async def extract(
        self,
        session_id: str,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> "AreaEpisode | None":
        """
        Extrae un episodio estructurado de una sesión completa.
        Retorna None si la sesión no cumple criterios mínimos.
        """
        # Obtener mensajes de la sesión
        msgs_q = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        messages = msgs_q.scalars().all()

        # Criterios de skip
        user_msgs = [m for m in messages if m.role == "user"]
        bot_msgs = [m for m in messages if m.role == "assistant"]

        if len(user_msgs) < 2:
            await self._mark_session(session_id, "skipped_too_short", db)
            return None

        total_bot_length = sum(len(m.content) for m in bot_msgs)
        if total_bot_length < 60:
            await self._mark_session(session_id, "skipped_no_content", db)
            return None

        # Clasificar session_arc de forma determinística (sin LLM)
        session_arc = self._classify_arc(messages)

        # Construir transcripción
        transcript = self._build_transcript(messages)

        # Extraer episodio via LLM
        extracted = await self._extract_with_llm(transcript)
        if not extracted:
            await self._mark_session(session_id, "extraction_failed", db)
            logger.warning(f"CME: extracción fallida para sesión {session_id}")
            return None

        # Usar session_arc determinístico (más confiable que el del LLM)
        extracted["session_arc"] = session_arc

        # Generar embedding de situation
        situation_embedding = await get_embedding(extracted["situation"])

        # Extraer causal_explanation o failure_analysis según el arc
        causal_explanation = None
        failure_analysis = None

        if session_arc in ("resolved", "neutral"):
            causal_explanation = await self._extract_secondary(
                transcript, CAUSAL_PROMPT, max_chars=300
            )
        elif session_arc in ("abandoned", "degraded"):
            failure_analysis = await self._extract_secondary(
                transcript, FAILURE_PROMPT, max_chars=300
            )

        # Crear y guardar el episodio
        episode = AreaEpisode(
            area_id=area_id,
            tenant_id=tenant_id,
            session_id=session_id,
            situation=extracted["situation"][:400],
            strategy=extracted["strategy"][:400],
            outcome=extracted["outcome"][:300],
            session_arc=session_arc,
            situation_embedding=json.dumps(situation_embedding) if situation_embedding else None,
            quality_score=None,  # se calcula después por QualitySignalEngine
            temporal_weight=1.0,
            causal_explanation=causal_explanation,
            failure_analysis=failure_analysis,
            emotional_intensity=0.0,
            extraction_status="completed",
        )
        db.add(episode)
        await self._mark_session(session_id, "completed", db)
        await db.commit()
        await db.refresh(episode)

        logger.info(f"CME: episodio extraído para sesión {session_id} — arc={session_arc}")
        return episode

    def _classify_arc(self, messages: list) -> str:
        """
        Clasifica session_arc de forma determinística sin LLM.
        resolved: últimos 20% de mensajes contienen señal de resolución Y sin reformulación
        degraded: últimos 20% de mensajes < 15 chars promedio O contienen frustración
        abandoned: sesión termina sin mensaje de cierre Y silencio ≥ 20 min
        neutral: ninguno de los anteriores
        """
        from app.analytics import REPHRASED_SIGNALS

        user_msgs = [m for m in messages if m.role == "user"]
        if not user_msgs:
            return "neutral"

        # Calcular ventana del último 20%
        cutoff = max(1, len(user_msgs) - max(1, len(user_msgs) // 5))
        last_msgs = user_msgs[cutoff:]

        last_text = " ".join(m.content.lower() for m in last_msgs)
        all_text = " ".join(m.content.lower() for m in user_msgs)

        has_resolution = any(s in last_text for s in RESOLUTION_SIGNALS)
        has_rephrasing = any(s in all_text for s in REPHRASED_SIGNALS)
        has_frustration = any(s in last_text for s in FRUSTRATION_SIGNALS)

        # Verificar abandono (silencio ≥ 20 min tras última respuesta del bot)
        bot_msgs = [m for m in messages if m.role == "assistant"]
        is_abandoned = False
        if bot_msgs and user_msgs:
            last_bot = bot_msgs[-1]
            last_user = user_msgs[-1]
            try:
                t_bot = last_bot.created_at
                t_user = last_user.created_at
                if t_bot.tzinfo is None:
                    t_bot = t_bot.replace(tzinfo=timezone.utc)
                if t_user.tzinfo is None:
                    t_user = t_user.replace(tzinfo=timezone.utc)
                # Si el último mensaje del bot es posterior al último del usuario
                # y no hay señal de cierre → posible abandono
                if t_bot > t_user and not has_resolution:
                    if (t_bot - t_user) >= timedelta(minutes=20):
                        is_abandoned = True
            except Exception:
                pass

        if has_resolution and not has_rephrasing:
            return "resolved"

        avg_last_len = sum(len(m.content) for m in last_msgs) / max(len(last_msgs), 1)
        if avg_last_len < 15 or has_frustration:
            return "degraded"

        if is_abandoned:
            return "abandoned"

        return "neutral"

    def _build_transcript(self, messages: list) -> str:
        """Construye la transcripción de la sesión para el LLM."""
        lines = []
        for m in messages[-30:]:  # últimos 30 mensajes para no exceder tokens
            role = "Usuario" if m.role == "user" else "Bot"
            lines.append(f"{role}: {m.content[:200]}")
        return "\n".join(lines)

    async def _extract_with_llm(self, transcript: str) -> dict | None:
        """Llama al LLM para extraer el episodio en JSON. Reintenta con prompt simplificado."""
        # Intento 1: prompt completo
        result = await self._call_llm_json(
            EXTRACT_PROMPT.format(transcript=transcript)
        )
        if result:
            return result

        # Intento 2: prompt simplificado
        result = await self._call_llm_json(
            EXTRACT_PROMPT_SIMPLE.format(transcript=transcript)
        )
        return result

    async def _call_llm_json(self, prompt: str) -> dict | None:
        """Llama al LLM y parsea la respuesta como JSON."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                headers = {"Content-Type": "application/json"}
                if settings.LLM_API_KEY and settings.LLM_API_KEY != "ollama":
                    headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

                res = await client.post(
                    settings.LLM_API_URL,
                    headers=headers,
                    json={
                        "model": settings.LLM_MODEL,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )
                if res.status_code != 200:
                    return None

                content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if not content:
                    return None

                # Extraer JSON del contenido (puede venir con texto alrededor)
                content = content.strip()
                if "```" in content:
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]

                data = json.loads(content.strip())

                # Validar campos requeridos
                required = {"situation", "strategy", "outcome", "session_arc"}
                if not required.issubset(data.keys()):
                    return None

                # Validar session_arc
                valid_arcs = {"resolved", "degraded", "neutral", "abandoned"}
                if data.get("session_arc") not in valid_arcs:
                    data["session_arc"] = "neutral"

                return data
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"CME: error parseando JSON del LLM: {e}")
            return None

    async def _extract_secondary(self, transcript: str, prompt: str, max_chars: int = 300) -> str | None:
        """Extrae explicación causal o análisis de fallo."""
        try:
            full_prompt = f"{prompt}\n\nConversación:\n{transcript[-500:]}"
            async with httpx.AsyncClient(timeout=30) as client:
                headers = {"Content-Type": "application/json"}
                if settings.LLM_API_KEY and settings.LLM_API_KEY != "ollama":
                    headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

                res = await client.post(
                    settings.LLM_API_URL,
                    headers=headers,
                    json={
                        "model": settings.LLM_MODEL,
                        "messages": [{"role": "user", "content": full_prompt}]
                    }
                )
                if res.status_code != 200:
                    return None

                content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if content and len(content.strip()) >= 10:
                    return content.strip()[:max_chars]
        except Exception:
            pass
        return None

    async def _mark_session(self, session_id: str, status: str, db: AsyncSession) -> None:
        """Actualiza cme_extraction_status en ChatSession."""
        try:
            session_q = await db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            session = session_q.scalar_one_or_none()
            if session:
                session.cme_extraction_status = status
                await db.commit()
        except Exception as e:
            logger.warning(f"CME: no se pudo actualizar extraction_status: {e}")


# Instancia global singleton
episode_extractor = EpisodeExtractor()
