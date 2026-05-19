"""
Forgetting Curve — Decaimiento exponencial del temporal_weight de episodios.

Fórmula: temporal_weight = e^(-λ × días_desde_último_refuerzo)
λ default = 0.01 → vida media ≈ 69 días

Día 0:   1.00  (conocimiento fresco)
Día 30:  ≈ 0.74
Día 69:  ≈ 0.50  (vida media)
Día 100: ≈ 0.37
Día 230: ≈ 0.10  (umbral de exclusión del Context Enricher)
"""
import math
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaEpisode

logger = logging.getLogger(__name__)

EXCLUSION_THRESHOLD = 0.1  # episodios con temporal_weight < 0.1 se excluyen del enrichment


class ForgettingCurve:

    def compute_weight(
        self,
        days_since_reinforcement: float,
        lambda_rate: float = 0.01
    ) -> float:
        """
        Calcula temporal_weight usando decaimiento exponencial.
        temporal_weight = e^(-λ × días)
        Resultado siempre en (0.0, 1.0].
        """
        if days_since_reinforcement < 0:
            days_since_reinforcement = 0.0
        weight = math.exp(-lambda_rate * days_since_reinforcement)
        return round(max(0.0001, min(1.0, weight)), 6)

    async def apply_decay_for_area(
        self,
        area_id: str,
        lambda_rate: float,
        db: AsyncSession
    ) -> int:
        """
        Aplica decaimiento exponencial a todos los episodios del área.
        Excluye episodios ya por debajo del umbral (temporal_weight < 0.1).
        Retorna el número de episodios actualizados.
        """
        now = datetime.now(timezone.utc)

        # Obtener episodios con temporal_weight >= umbral de exclusión
        episodes_q = await db.execute(
            select(AreaEpisode)
            .where(
                AreaEpisode.area_id == area_id,
                AreaEpisode.temporal_weight >= EXCLUSION_THRESHOLD
            )
        )
        episodes = episodes_q.scalars().all()

        updated_count = 0
        for episode in episodes:
            try:
                created = episode.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)

                days_elapsed = (now - created).total_seconds() / 86400.0
                new_weight = self.compute_weight(days_elapsed, lambda_rate)
                episode.temporal_weight = new_weight
                updated_count += 1
            except Exception as e:
                logger.warning(f"CME ForgettingCurve: error actualizando episodio {episode.id}: {e}")

        if updated_count > 0:
            await db.commit()
            logger.info(f"CME ForgettingCurve: {updated_count} episodios actualizados para área {area_id}")

        return updated_count

    async def reinforce_episode(self, episode_id: str, db: AsyncSession) -> None:
        """
        Resetea temporal_weight a 1.0 cuando el episodio es referenciado
        en una sesión exitosa (quality_score ≥ 0.6).
        """
        try:
            ep_q = await db.execute(
                select(AreaEpisode).where(AreaEpisode.id == episode_id)
            )
            episode = ep_q.scalar_one_or_none()
            if episode:
                episode.temporal_weight = 1.0
                await db.commit()
                logger.debug(f"CME ForgettingCurve: episodio {episode_id} reforzado → temporal_weight=1.0")
        except Exception as e:
            logger.warning(f"CME ForgettingCurve: error reforzando episodio {episode_id}: {e}")

    def is_excluded(self, temporal_weight: float) -> bool:
        """Retorna True si el episodio debe excluirse del Context Enricher."""
        return temporal_weight < EXCLUSION_THRESHOLD

    def compute_relevance_score(
        self,
        cosine_similarity: float,
        temporal_weight: float,
        emotional_intensity: float = 0.0
    ) -> float:
        """
        Calcula el score de relevancia combinado para ranking en el Context Enricher.
        relevance = cosine × temporal_weight × (1 + emotional_intensity × 0.3)
        El boost emocional es parte de la Selective Attention (Fase 2).
        """
        base = cosine_similarity * temporal_weight
        boost = 1.0 + (emotional_intensity * 0.3)
        return round(base * boost, 6)


# Instancia global singleton
forgetting_curve = ForgettingCurve()
