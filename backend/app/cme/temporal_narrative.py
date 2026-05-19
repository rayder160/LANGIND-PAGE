"""
Temporal Narrative — Detecta cadenas causales temporales entre episodios del área.

Busca pares de episodios con:
- Similitud coseno >= 0.65 en situation_embedding
- Diferencia temporal entre 1 y 30 días

Genera causal_link_description via LLM para cada cadena detectada.

Verificar settings.CME_ENABLE_TEMPORAL_NARRATIVE antes de ejecutar.
"""
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaEpisode, TemporalChain as TemporalChainModel
from app.rag import cosine_similarity
from app.config import settings

logger = logging.getLogger(__name__)

CHAIN_MIN_SIMILARITY = 0.65
CHAIN_QUERY_SIMILARITY = 0.60
CHAIN_MIN_DAYS = 1
CHAIN_MAX_DAYS = 30
MAX_EPISODES_TO_COMPARE = 50
LOOKBACK_DAYS = 90


class TemporalNarrative:

    async def detect_chains(
        self,
        area_id: str,
        db: AsyncSession
    ) -> list[TemporalChainModel]:
        """
        Detecta cadenas causales temporales entre episodios del área.

        Criterios:
        - Similitud coseno >= 0.65 en situation_embedding
        - Diferencia temporal entre 1 y 30 días
        - No existe ya una TemporalChain para ese par

        Genera causal_link_description via LLM.
        Retorna lista de TemporalChain creadas.
        """
        if not settings.CME_ENABLE_TEMPORAL_NARRATIVE:
            return []

        chains_created = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

        try:
            episodes_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.situation_embedding.isnot(None),
                    AreaEpisode.extraction_status == "completed",
                    AreaEpisode.created_at >= cutoff,
                )
                .order_by(AreaEpisode.created_at)
                .limit(MAX_EPISODES_TO_COMPARE)
            )
            episodes = episodes_q.scalars().all()

            if len(episodes) < 2:
                return []

            # Parsear embeddings
            eps_with_emb = []
            for ep in episodes:
                try:
                    emb = json.loads(ep.situation_embedding)
                    eps_with_emb.append((ep, emb))
                except Exception:
                    continue

            for i in range(len(eps_with_emb)):
                ep_a, emb_a = eps_with_emb[i]

                for j in range(i + 1, len(eps_with_emb)):
                    ep_b, emb_b = eps_with_emb[j]

                    # Verificar diferencia temporal
                    created_a = ep_a.created_at
                    created_b = ep_b.created_at
                    if created_a.tzinfo is None:
                        created_a = created_a.replace(tzinfo=timezone.utc)
                    if created_b.tzinfo is None:
                        created_b = created_b.replace(tzinfo=timezone.utc)

                    delta_days = abs((created_b - created_a).total_seconds()) / 86400.0
                    if delta_days < CHAIN_MIN_DAYS or delta_days > CHAIN_MAX_DAYS:
                        continue

                    # Verificar similitud
                    sim = cosine_similarity(emb_a, emb_b)
                    if sim < CHAIN_MIN_SIMILARITY:
                        continue

                    # Verificar que no existe ya esta cadena
                    existing_q = await db.execute(
                        select(TemporalChainModel)
                        .where(
                            TemporalChainModel.area_id == area_id,
                            (
                                (TemporalChainModel.episode_a_id == ep_a.id) &
                                (TemporalChainModel.episode_b_id == ep_b.id)
                            ) | (
                                (TemporalChainModel.episode_a_id == ep_b.id) &
                                (TemporalChainModel.episode_b_id == ep_a.id)
                            ),
                        )
                    )
                    if existing_q.scalar_one_or_none():
                        continue

                    # Generar descripción del vínculo causal
                    causal_desc = await self._generate_causal_link(ep_a, ep_b, delta_days)

                    chain = TemporalChainModel(
                        area_id=area_id,
                        episode_a_id=ep_a.id,
                        episode_b_id=ep_b.id,
                        time_delta_days=round(delta_days, 2),
                        causal_link_description=causal_desc,
                        confidence=round(sim, 4),
                    )
                    db.add(chain)
                    chains_created.append(chain)

            if chains_created:
                await db.commit()
                logger.info(
                    f"CME TemporalNarrative: {len(chains_created)} cadenas temporales "
                    f"detectadas para área {area_id}"
                )

        except Exception as e:
            logger.warning(f"CME TemporalNarrative: error en detect_chains: {e}")

        return chains_created

    async def get_relevant_chains(
        self,
        query_embedding: list[float],
        area_id: str,
        db: AsyncSession
    ) -> list[TemporalChainModel]:
        """
        Retorna chains donde episode_b tiene similitud >= 0.60 con el query.
        Estas cadenas son relevantes porque el episodio más reciente (b) es similar al query.
        """
        if not settings.CME_ENABLE_TEMPORAL_NARRATIVE:
            return []

        try:
            # Obtener todas las chains del área
            chains_q = await db.execute(
                select(TemporalChainModel)
                .where(TemporalChainModel.area_id == area_id)
                .order_by(TemporalChainModel.confidence.desc())
                .limit(50)
            )
            chains = chains_q.scalars().all()

            if not chains:
                return []

            # Obtener embeddings de los episode_b
            episode_b_ids = [c.episode_b_id for c in chains]
            episodes_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.id.in_(episode_b_ids),
                    AreaEpisode.situation_embedding.isnot(None),
                )
            )
            episodes = episodes_q.scalars().all()

            episode_emb_map = {}
            for ep in episodes:
                try:
                    episode_emb_map[ep.id] = json.loads(ep.situation_embedding)
                except Exception:
                    continue

            # Filtrar chains donde episode_b es relevante para el query
            relevant = []
            for chain in chains:
                emb_b = episode_emb_map.get(chain.episode_b_id)
                if not emb_b:
                    continue

                sim = cosine_similarity(query_embedding, emb_b)
                if sim >= CHAIN_QUERY_SIMILARITY:
                    relevant.append(chain)

            return relevant[:3]  # máximo 3 chains por query

        except Exception as e:
            logger.warning(f"CME TemporalNarrative: error en get_relevant_chains: {e}")
            return []

    async def _generate_causal_link(
        self,
        ep_a: AreaEpisode,
        ep_b: AreaEpisode,
        delta_days: float
    ) -> str:
        """Genera descripción del vínculo causal temporal entre dos episodios via LLM."""
        fallback = (
            f"Episodios relacionados con {delta_days:.0f} días de diferencia. "
            f"El primero puede ser antecedente del segundo."
        )

        try:
            prompt = (
                f"Dos episodios similares ocurrieron con {delta_days:.0f} días de diferencia.\n"
                f"Episodio anterior: {ep_a.situation[:200]}\n"
                f"Episodio posterior: {ep_b.situation[:200]}\n\n"
                f"¿Cuál es el posible vínculo causal? Explica en 1-2 oraciones (≤300 chars). "
                f"Solo la explicación."
            )

            async with httpx.AsyncClient(timeout=15) as client:
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
            logger.debug(f"CME TemporalNarrative: LLM falló para causal link: {e}")

        return fallback


# Instancia global singleton
temporal_narrative = TemporalNarrative()
