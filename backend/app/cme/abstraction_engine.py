"""
Abstraction Engine — Promueve Patrones a Principios (3 niveles de abstracción).
También detecta conexiones inesperadas cross-dominio durante la consolidación nocturna.
"""
import json
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaPattern, AreaEpisode, CrossDomainInsight
from app.rag import cosine_similarity, get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

PRINCIPLE_SIMILARITY_THRESHOLD = 0.70
PRINCIPLE_CONFIDENCE_THRESHOLD = 0.65
MIN_PATTERNS_FOR_PRINCIPLE = 5
CROSS_DOMAIN_MIN_SIM = 0.45
CROSS_DOMAIN_MAX_SIM = 0.65


class AbstractionEngine:

    async def evaluate_promotion(
        self,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> list:
        """
        Busca grupos de ≥5 patrones con cosine similarity ≥ 0.70 y confidence ≥ 0.65.
        Crea un Principio (abstraction_level=3) por cada grupo.
        Actualiza los patrones contribuyentes con parent_principle_id y abstraction_level=2.
        Retorna lista de principios creados.
        """
        created_principles = []
        try:
            # Obtener patrones aprobados de nivel 1 con embedding y confidence suficiente
            patterns_q = await db.execute(
                select(AreaPattern)
                .where(
                    AreaPattern.area_id == area_id,
                    AreaPattern.is_approved == True,
                    AreaPattern.abstraction_level == 1,
                    AreaPattern.confidence_score >= PRINCIPLE_CONFIDENCE_THRESHOLD,
                    AreaPattern.trigger_embedding.isnot(None),
                    AreaPattern.parent_principle_id.is_(None)  # no ya vinculados
                )
            )
            patterns = patterns_q.scalars().all()

            if len(patterns) < MIN_PATTERNS_FOR_PRINCIPLE:
                return []

            # Parsear embeddings
            patterns_with_emb = []
            for p in patterns:
                try:
                    emb = json.loads(p.trigger_embedding)
                    patterns_with_emb.append((p, emb))
                except Exception:
                    continue

            if len(patterns_with_emb) < MIN_PATTERNS_FOR_PRINCIPLE:
                return []

            # Agrupar patrones similares
            groups = self._cluster_patterns(patterns_with_emb, PRINCIPLE_SIMILARITY_THRESHOLD)

            for group in groups:
                principle = await self._create_principle(group, area_id, tenant_id, db)
                if principle:
                    created_principles.append(principle)

            logger.info(f"CME AbstractionEngine: {len(created_principles)} principios creados para área {area_id}")

        except Exception as e:
            logger.error(f"CME AbstractionEngine: error en evaluate_promotion: {e}")

        return created_principles

    async def cross_domain_cluster(
        self,
        area_id: str,
        db: AsyncSession
    ) -> list:
        """
        Detecta conexiones inesperadas entre episodios de dominios distintos.
        Umbral: cosine similarity entre 0.45 y 0.65 (similar pero no obvio).
        Retorna lista de CrossDomainInsight creados.
        """
        insights_created = []
        try:
            # Obtener episodios con embedding del área
            eps_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.situation_embedding.isnot(None),
                    AreaEpisode.extraction_status == "completed"
                )
                .order_by(AreaEpisode.created_at.desc())
                .limit(100)  # últimos 100 episodios para eficiencia
            )
            episodes = eps_q.scalars().all()

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
                    if CROSS_DOMAIN_MIN_SIM <= sim <= CROSS_DOMAIN_MAX_SIM:
                        # Verificar que no existe ya este insight
                        existing_q = await db.execute(
                            select(CrossDomainInsight)
                            .where(
                                CrossDomainInsight.area_id == area_id,
                                (
                                    (CrossDomainInsight.episode_a_id == ep_a.id) &
                                    (CrossDomainInsight.episode_b_id == ep_b.id)
                                ) | (
                                    (CrossDomainInsight.episode_a_id == ep_b.id) &
                                    (CrossDomainInsight.episode_b_id == ep_a.id)
                                )
                            )
                        )
                        if existing_q.scalar_one_or_none():
                            continue

                        # Generar descripción de la conexión via LLM
                        connection_desc = await self._generate_connection_description(ep_a, ep_b)
                        if not connection_desc:
                            continue

                        insight = CrossDomainInsight(
                            area_id=area_id,
                            episode_a_id=ep_a.id,
                            episode_b_id=ep_b.id,
                            connection_description=connection_desc,
                            confidence=sim,
                            status="pending",
                        )
                        db.add(insight)
                        insights_created.append(insight)

            if insights_created:
                await db.commit()
                logger.info(f"CME AbstractionEngine: {len(insights_created)} conexiones cross-dominio detectadas para área {area_id}")

        except Exception as e:
            logger.error(f"CME AbstractionEngine: error en cross_domain_cluster: {e}")

        return insights_created

    def _cluster_patterns(
        self,
        patterns_with_emb: list[tuple],
        threshold: float
    ) -> list[list[tuple]]:
        """Agrupa patrones por cosine similarity ≥ threshold. Retorna grupos de ≥ MIN_PATTERNS_FOR_PRINCIPLE."""
        if not patterns_with_emb:
            return []

        assigned = [False] * len(patterns_with_emb)
        groups = []

        for i, (p_i, emb_i) in enumerate(patterns_with_emb):
            if assigned[i]:
                continue

            group = [(p_i, emb_i)]
            assigned[i] = True

            for j, (p_j, emb_j) in enumerate(patterns_with_emb):
                if assigned[j] or i == j:
                    continue
                sim = cosine_similarity(emb_i, emb_j)
                if sim >= threshold:
                    group.append((p_j, emb_j))
                    assigned[j] = True

            if len(group) >= MIN_PATTERNS_FOR_PRINCIPLE:
                groups.append(group)

        return groups

    async def _create_principle(
        self,
        group: list[tuple],
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> "AreaPattern | None":
        """Crea un Principio (abstraction_level=3) a partir de un grupo de patrones."""
        patterns = [p for p, _ in group]
        embeddings = [emb for _, emb in group]

        # Generar descripción del principio via LLM
        principle_desc = await self._synthesize_principle(patterns)
        if not principle_desc:
            return None

        # Embedding promedio del grupo
        avg_emb = self._average_embeddings(embeddings)

        # Calcular confidence promedio del grupo
        avg_confidence = sum(p.confidence_score for p in patterns) / len(patterns)

        principle = AreaPattern(
            area_id=area_id,
            tenant_id=tenant_id,
            trigger_description=principle_desc,
            trigger_embedding=json.dumps(avg_emb) if avg_emb else None,
            response_description=f"Principio derivado de {len(patterns)} patrones del área.",
            confidence_score=round(avg_confidence, 4),
            diversity_score=max(p.diversity_score for p in patterns),
            episode_count=sum(p.episode_count for p in patterns),
            distinct_user_count=max(p.distinct_user_count for p in patterns),
            abstraction_level=3,
            is_approved=False,
            source_episode_ids="[]",
        )
        db.add(principle)
        await db.flush()  # obtener ID sin commit completo

        # Vincular patrones contribuyentes
        for p in patterns:
            p.abstraction_level = 2
            p.parent_principle_id = principle.id

        await db.commit()
        await db.refresh(principle)
        logger.info(f"CME AbstractionEngine: principio creado {principle.id} desde {len(patterns)} patrones")
        return principle

    async def _synthesize_principle(self, patterns: list) -> str | None:
        """Genera la descripción del principio via LLM."""
        try:
            sample = "\n".join(
                f"- {p.trigger_description[:150]}"
                for p in patterns[:7]
            )
            prompt = (
                f"Estos {len(patterns)} patrones del área comparten una regularidad común. "
                f"Genera una regla organizacional generalizada (principio) en 1-2 oraciones (≤300 chars):\n\n"
                f"{sample}\n\nSolo el principio, sin texto adicional."
            )
            async with httpx.AsyncClient(timeout=30) as client:
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
                if res.status_code == 200:
                    content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content and len(content.strip()) >= 10:
                        return content.strip()[:300]
        except Exception as e:
            logger.debug(f"CME AbstractionEngine: error sintetizando principio: {e}")
        return None

    async def _generate_connection_description(self, ep_a, ep_b) -> str | None:
        """Genera descripción de la conexión inesperada entre dos episodios via LLM."""
        try:
            prompt = (
                f"Estos dos episodios de dominios distintos parecen tener una raíz común. "
                f"¿Cuál es la conexión? Explica en 1-2 oraciones (≤300 chars):\n\n"
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
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )
                if res.status_code == 200:
                    content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content and len(content.strip()) >= 10:
                        return content.strip()[:300]
        except Exception:
            pass
        return None

    def _average_embeddings(self, embeddings: list[list[float]]) -> list[float] | None:
        """Calcula el embedding promedio."""
        if not embeddings:
            return None
        dim = len(embeddings[0])
        avg = [0.0] * dim
        for emb in embeddings:
            for i, v in enumerate(emb):
                avg[i] += v
        n = len(embeddings)
        return [v / n for v in avg]


# Instancia global singleton
abstraction_engine = AbstractionEngine()
