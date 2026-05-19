"""
Cross Domain Insights — Detecta conexiones inesperadas entre episodios de dominios distintos.

Umbral de similitud: entre 0.45 y 0.65 (similar pero no obvio → conexión inesperada).
Las conexiones validadas se inyectan en el contexto cuando el query toca alguno de los dominios.

Verificar settings.CME_ENABLE_CROSS_DOMAIN_INSIGHTS antes de ejecutar.
"""
import json
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaEpisode, CrossDomainInsight as CrossDomainInsightModel
from app.rag import cosine_similarity
from app.config import settings

logger = logging.getLogger(__name__)

CROSS_DOMAIN_MIN_SIM = 0.45
CROSS_DOMAIN_MAX_SIM = 0.65
QUERY_RELEVANCE_THRESHOLD = 0.55
MAX_EPISODES_TO_COMPARE = 100


class CrossDomainInsights:

    async def detect_connections(
        self,
        area_id: str,
        db: AsyncSession
    ) -> list[CrossDomainInsightModel]:
        """
        Compara embeddings de episodios del área y detecta conexiones inesperadas.
        Umbral: cosine entre 0.45 y 0.65.
        Genera connection_description via LLM para cada par detectado.
        Retorna lista de CrossDomainInsight creados.
        """
        if not settings.CME_ENABLE_CROSS_DOMAIN_INSIGHTS:
            return []

        insights_created = []
        try:
            # Obtener episodios recientes con embedding
            episodes_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.situation_embedding.isnot(None),
                    AreaEpisode.extraction_status == "completed",
                )
                .order_by(AreaEpisode.created_at.desc())
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

            # Detectar pares con similitud en rango [0.45, 0.65]
            for i in range(len(eps_with_emb)):
                for j in range(i + 1, len(eps_with_emb)):
                    ep_a, emb_a = eps_with_emb[i]
                    ep_b, emb_b = eps_with_emb[j]

                    sim = cosine_similarity(emb_a, emb_b)
                    if not (CROSS_DOMAIN_MIN_SIM <= sim <= CROSS_DOMAIN_MAX_SIM):
                        continue

                    # Verificar que no existe ya este insight
                    existing_q = await db.execute(
                        select(CrossDomainInsightModel)
                        .where(
                            CrossDomainInsightModel.area_id == area_id,
                            (
                                (CrossDomainInsightModel.episode_a_id == ep_a.id) &
                                (CrossDomainInsightModel.episode_b_id == ep_b.id)
                            ) | (
                                (CrossDomainInsightModel.episode_a_id == ep_b.id) &
                                (CrossDomainInsightModel.episode_b_id == ep_a.id)
                            ),
                        )
                    )
                    if existing_q.scalar_one_or_none():
                        continue

                    # Generar descripción de la conexión via LLM
                    connection_desc = await self._generate_connection_description(ep_a, ep_b)
                    if not connection_desc:
                        continue

                    insight = CrossDomainInsightModel(
                        area_id=area_id,
                        episode_a_id=ep_a.id,
                        episode_b_id=ep_b.id,
                        connection_description=connection_desc,
                        confidence=round(sim, 4),
                        status="pending",
                    )
                    db.add(insight)
                    insights_created.append(insight)

            if insights_created:
                await db.commit()
                logger.info(
                    f"CME CrossDomainInsights: {len(insights_created)} conexiones "
                    f"detectadas para área {area_id}"
                )

        except Exception as e:
            logger.warning(f"CME CrossDomainInsights: error en detect_connections: {e}")

        return insights_created

    async def get_relevant_insights(
        self,
        query_embedding: list[float],
        area_id: str,
        db: AsyncSession
    ) -> list[CrossDomainInsightModel]:
        """
        Retorna insights validados donde el query toca alguno de los dominios.
        Compara el query_embedding contra los embeddings de los episodios de cada insight.
        """
        if not settings.CME_ENABLE_CROSS_DOMAIN_INSIGHTS:
            return []

        try:
            # Obtener insights validados del área
            insights_q = await db.execute(
                select(CrossDomainInsightModel)
                .where(
                    CrossDomainInsightModel.area_id == area_id,
                    CrossDomainInsightModel.status == "validated",
                )
            )
            insights = insights_q.scalars().all()

            if not insights:
                return []

            # Obtener todos los episode_ids involucrados
            episode_ids = set()
            for insight in insights:
                episode_ids.add(insight.episode_a_id)
                episode_ids.add(insight.episode_b_id)

            # Cargar embeddings de esos episodios
            episodes_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.id.in_(list(episode_ids)),
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

            # Filtrar insights donde el query es relevante para alguno de los episodios
            relevant = []
            for insight in insights:
                emb_a = episode_emb_map.get(insight.episode_a_id)
                emb_b = episode_emb_map.get(insight.episode_b_id)

                is_relevant = False
                if emb_a:
                    sim_a = cosine_similarity(query_embedding, emb_a)
                    if sim_a >= QUERY_RELEVANCE_THRESHOLD:
                        is_relevant = True
                if not is_relevant and emb_b:
                    sim_b = cosine_similarity(query_embedding, emb_b)
                    if sim_b >= QUERY_RELEVANCE_THRESHOLD:
                        is_relevant = True

                if is_relevant:
                    relevant.append(insight)

            return relevant[:3]  # máximo 3 insights por query

        except Exception as e:
            logger.warning(f"CME CrossDomainInsights: error en get_relevant_insights: {e}")
            return []

    async def _generate_connection_description(
        self,
        ep_a: AreaEpisode,
        ep_b: AreaEpisode
    ) -> str | None:
        """Genera descripción de la conexión inesperada entre dos episodios via LLM."""
        fallback = (
            f"Conexión inesperada entre '{ep_a.situation[:80]}' "
            f"y '{ep_b.situation[:80]}'."
        )

        try:
            prompt = (
                f"Estos dos episodios de dominios distintos parecen tener una raíz común. "
                f"¿Cuál es la conexión inesperada? Explica en 1-2 oraciones (≤300 chars):\n\n"
                f"Episodio A: {ep_a.situation[:200]}\n"
                f"Episodio B: {ep_b.situation[:200]}\n\n"
                f"Solo la explicación, sin texto adicional."
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
            logger.debug(f"CME CrossDomainInsights: LLM falló para conexión: {e}")

        return fallback


# Instancia global singleton
cross_domain_insights = CrossDomainInsights()
