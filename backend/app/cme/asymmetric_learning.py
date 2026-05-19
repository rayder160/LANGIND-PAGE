"""
Asymmetric Learning — Aprende más de los fracasos que de los éxitos.

Los episodios de fallo reciben mayor peso en el aprendizaje y se analizan
en detalle para extraer lecciones específicas.

Verificar settings.CME_ENABLE_ASYMMETRIC_LEARNING antes de ejecutar.
"""
import json
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaEpisode
from app.config import settings

logger = logging.getLogger(__name__)

FAILURE_ARCS = ("abandoned", "degraded")
FAILURE_WEIGHT_MULTIPLIER = 1.5
SUCCESS_WEIGHT = 1.0


class AsymmetricLearning:

    async def extract_failure_analysis(
        self,
        session_id: str,
        db: AsyncSession
    ) -> str | None:
        """
        Extrae análisis de fallo para sesiones con arc = abandoned/degraded.
        Llama al LLM: "¿Qué salió mal y por qué? (≤300 chars)"
        Guarda el resultado en episode.failure_analysis.
        Retorna el análisis o None si no aplica.
        """
        if not settings.CME_ENABLE_ASYMMETRIC_LEARNING:
            return None

        try:
            episode_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.session_id == session_id,
                    AreaEpisode.extraction_status == "completed",
                )
            )
            episode = episode_q.scalar_one_or_none()

            if not episode:
                return None

            # Solo para sesiones fallidas
            if episode.session_arc not in FAILURE_ARCS:
                return None

            # Si ya tiene análisis, retornarlo
            if episode.failure_analysis:
                return episode.failure_analysis

            # Generar análisis via LLM
            analysis = await self._generate_failure_analysis(episode)

            if analysis:
                episode.failure_analysis = analysis[:300]
                await db.commit()
                logger.debug(
                    f"CME AsymmetricLearning: failure_analysis extraído para sesión {session_id}"
                )

            return episode.failure_analysis

        except Exception as e:
            logger.warning(f"CME AsymmetricLearning: error en extract_failure_analysis: {e}")
            return None

    async def _generate_failure_analysis(self, episode: AreaEpisode) -> str | None:
        """Genera el análisis de fallo via LLM."""
        fallback = (
            f"Sesión {episode.session_arc}: la estrategia '{episode.strategy[:100]}' "
            f"no resolvió la situación '{episode.situation[:100]}'."
        )

        try:
            prompt = (
                f"Una sesión de asistencia terminó en {episode.session_arc}.\n"
                f"Situación: {episode.situation[:300]}\n"
                f"Estrategia usada: {episode.strategy[:300]}\n"
                f"Resultado: {episode.outcome[:200]}\n\n"
                f"¿Qué salió mal y por qué? Responde en 1-2 oraciones (≤300 chars). "
                f"Solo el análisis, sin texto adicional."
            )

            async with httpx.AsyncClient(timeout=20) as client:
                headers = {"Content-Type": "application/json"}
                if settings.LLM_API_KEY and settings.LLM_API_KEY != "ollama":
                    headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

                res = await client.post(
                    settings.LLM_API_URL,
                    headers=headers,
                    json={
                        "model": settings.LLM_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if res.status_code == 200:
                    content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    content = content.strip()
                    if content and len(content) >= 10:
                        return content[:300]

        except Exception as e:
            logger.debug(f"CME AsymmetricLearning: LLM falló para failure_analysis: {e}")

        return fallback

    def apply_failure_weight(
        self,
        episodes: list
    ) -> list[tuple]:
        """
        Aplica pesos asimétricos a los episodios:
        - failure episodes (abandoned/degraded) × 1.5
        - success episodes × 1.0

        Retorna lista de (episode, weight).
        """
        if not settings.CME_ENABLE_ASYMMETRIC_LEARNING:
            return [(ep, SUCCESS_WEIGHT) for ep in episodes]

        weighted = []
        for episode in episodes:
            if episode.session_arc in FAILURE_ARCS:
                weight = FAILURE_WEIGHT_MULTIPLIER
            else:
                weight = SUCCESS_WEIGHT
            weighted.append((episode, weight))

        return weighted

    def get_failure_context_annotation(self, pattern) -> str:
        """
        Retorna una anotación de contexto para patrones de fallo.
        Si is_failure_pattern: "evitar este enfoque: [trigger]"
        """
        if not settings.CME_ENABLE_ASYMMETRIC_LEARNING:
            return ""

        if not pattern or not getattr(pattern, "is_failure_pattern", False):
            return ""

        trigger = getattr(pattern, "trigger_description", "")[:150]
        return f"evitar este enfoque: {trigger}"


# Instancia global singleton
asymmetric_learning = AsymmetricLearning()
