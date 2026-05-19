"""
User Brain — Espacio cognitivo privado por usuario.

Principio de diseño (experimento de doble rendija):
  Cada usuario es un observador independiente. Su función de onda cognitiva
  no colapsa por la observación de otros. Los episodios y patrones de cada
  instancia son invisibles para el resto del sistema.

  El flujo es unidireccional:
    UserBrain → CoreBrain (write-only, sin retroalimentación)

  El Context Enricher, cuando CME_EXPERIMENTAL_USER_ISOLATION=True,
  consulta SOLO la instancia del usuario actual. Nunca el área compartida,
  nunca el CoreBrain, nunca otra instancia.

Verificar settings.CME_EXPERIMENTAL_USER_ISOLATION antes de ejecutar.
"""
import json
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.cme import (
    UserEpisode, UserPattern,
    AreaEpisode, AreaPattern,  # solo para escritura espejo si se necesita
)
from app.rag import cosine_similarity, get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

# Umbrales — espejo de los del PatternDetector pero scoped por usuario
USER_CLUSTER_SIMILARITY = 0.75
USER_MIN_CLUSTER_SIZE = 2          # más bajo que el área: un usuario genera menos volumen
USER_FAILURE_WEIGHT = 1.5
USER_PATTERN_DETECTION_INTERVAL = 5  # cada 5 episodios (menos volumen que área)


class UserBrain:
    """
    Gestiona el espacio cognitivo privado de un usuario.
    Episodios, patrones y detección de cadenas temporales — todo scoped por user_id.
    """

    # ── Episodios ─────────────────────────────────────────────────────────────

    async def save_episode(
        self,
        area_episode: AreaEpisode,
        user_id: str,
        db: AsyncSession
    ) -> UserEpisode | None:
        """
        Crea un UserEpisode a partir de un AreaEpisode ya extraído.
        El UserEpisode es una copia privada — no referencia al AreaEpisode.
        Retorna el UserEpisode creado o None si falla.
        """
        if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
            return None

        try:
            episode = UserEpisode(
                user_id=user_id,
                area_id=area_episode.area_id,
                tenant_id=area_episode.tenant_id,
                session_id=area_episode.session_id,
                situation=area_episode.situation,
                strategy=area_episode.strategy,
                outcome=area_episode.outcome,
                session_arc=area_episode.session_arc,
                situation_embedding=area_episode.situation_embedding,
                quality_score=area_episode.quality_score,
                temporal_weight=1.0,
                causal_explanation=area_episode.causal_explanation,
                failure_analysis=area_episode.failure_analysis,
                emotional_intensity=area_episode.emotional_intensity,
                extraction_status=area_episode.extraction_status,
                promoted_to_core=False,
            )
            db.add(episode)
            await db.commit()
            await db.refresh(episode)

            logger.debug(
                f"CME UserBrain: episodio privado guardado para usuario {user_id} "
                f"(session {area_episode.session_id})"
            )

            # Disparar detección de patrones si corresponde
            await self._maybe_detect_patterns(user_id, area_episode.area_id, area_episode.tenant_id, db)

            return episode

        except Exception as e:
            logger.warning(f"CME UserBrain: error en save_episode para usuario {user_id}: {e}")
            return None

    # ── Consulta de episodios (solo para el usuario actual) ───────────────────

    async def query_episodes(
        self,
        query_embedding: list[float],
        user_id: str,
        area_id: str,
        db: AsyncSession,
        prioritize_resolved: bool = False,
        top_k: int = 3,
        min_relevance: float = 0.60
    ) -> list[tuple[UserEpisode, float]]:
        """
        Busca episodios privados del usuario por similitud × temporal_weight.
        NUNCA consulta episodios de otros usuarios.
        """
        if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
            return []

        try:
            eps_q = await db.execute(
                select(UserEpisode)
                .where(
                    UserEpisode.user_id == user_id,
                    UserEpisode.area_id == area_id,
                    UserEpisode.situation_embedding.isnot(None),
                    UserEpisode.temporal_weight >= 0.1,
                    UserEpisode.extraction_status == "completed",
                )
            )
            episodes = eps_q.scalars().all()

            scored = []
            for ep in episodes:
                try:
                    emb = json.loads(ep.situation_embedding)
                    cosine = cosine_similarity(query_embedding, emb)
                    # Relevancia combinada: similitud × peso temporal × boost emocional
                    emotional_boost = 1.0 + (ep.emotional_intensity or 0.0) * 0.2
                    relevance = cosine * ep.temporal_weight * emotional_boost
                    if relevance >= min_relevance:
                        scored.append((ep, relevance))
                except Exception:
                    continue

            if prioritize_resolved:
                scored.sort(key=lambda x: (x[0].session_arc == "resolved", x[1]), reverse=True)
            else:
                scored.sort(key=lambda x: x[1], reverse=True)

            return scored[:top_k]

        except Exception as e:
            logger.debug(f"CME UserBrain: error en query_episodes para usuario {user_id}: {e}")
            return []

    # ── Consulta de patrones (solo para el usuario actual) ────────────────────

    async def query_patterns(
        self,
        query_embedding: list[float],
        user_id: str,
        area_id: str,
        db: AsyncSession,
        top_k: int = 2,
        min_similarity: float = 0.65
    ) -> list[tuple[UserPattern, float]]:
        """
        Busca patrones privados del usuario.
        NUNCA consulta patrones de otros usuarios.
        """
        if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
            return []

        try:
            pats_q = await db.execute(
                select(UserPattern)
                .where(
                    UserPattern.user_id == user_id,
                    UserPattern.area_id == area_id,
                    UserPattern.trigger_embedding.isnot(None),
                )
            )
            patterns = pats_q.scalars().all()

            scored = []
            for p in patterns:
                try:
                    emb = json.loads(p.trigger_embedding)
                    sim = cosine_similarity(query_embedding, emb)
                    if sim >= min_similarity:
                        scored.append((p, sim))
                except Exception:
                    continue

            # Principios primero, luego por similitud
            scored.sort(key=lambda x: (x[0].abstraction_level, x[1]), reverse=True)
            return scored[:top_k]

        except Exception as e:
            logger.debug(f"CME UserBrain: error en query_patterns para usuario {user_id}: {e}")
            return []

    # ── Detección de patrones privados ────────────────────────────────────────

    async def _maybe_detect_patterns(
        self,
        user_id: str,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """
        Dispara detección de patrones cada USER_PATTERN_DETECTION_INTERVAL episodios.
        """
        try:
            count_q = await db.execute(
                select(func.count(UserEpisode.id))
                .where(
                    UserEpisode.user_id == user_id,
                    UserEpisode.area_id == area_id,
                    UserEpisode.extraction_status == "completed",
                )
            )
            count = count_q.scalar() or 0

            if count > 0 and count % USER_PATTERN_DETECTION_INTERVAL == 0:
                await self._detect_patterns(user_id, area_id, tenant_id, db)

        except Exception as e:
            logger.debug(f"CME UserBrain: error en _maybe_detect_patterns: {e}")

    async def _detect_patterns(
        self,
        user_id: str,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """
        Agrupa UserEpisodes del usuario en clusters por similitud coseno.
        Crea o actualiza UserPatterns para clusters de tamaño >= USER_MIN_CLUSTER_SIZE.
        """
        try:
            eps_q = await db.execute(
                select(UserEpisode)
                .where(
                    UserEpisode.user_id == user_id,
                    UserEpisode.area_id == area_id,
                    UserEpisode.situation_embedding.isnot(None),
                    UserEpisode.extraction_status == "completed",
                )
                .order_by(UserEpisode.created_at)
            )
            episodes = eps_q.scalars().all()

            if len(episodes) < USER_MIN_CLUSTER_SIZE:
                return

            eps_with_emb = []
            for ep in episodes:
                try:
                    emb = json.loads(ep.situation_embedding)
                    eps_with_emb.append((ep, emb))
                except Exception:
                    continue

            if len(eps_with_emb) < USER_MIN_CLUSTER_SIZE:
                return

            # Clustering greedy
            clusters = _cluster_episodes(eps_with_emb, USER_CLUSTER_SIMILARITY)

            for cluster in clusters:
                if len(cluster) < USER_MIN_CLUSTER_SIZE:
                    continue

                await self._process_cluster(cluster, user_id, area_id, tenant_id, db)

            logger.info(
                f"CME UserBrain: {len(clusters)} clusters procesados "
                f"para usuario {user_id}"
            )

        except Exception as e:
            logger.warning(f"CME UserBrain: error en _detect_patterns: {e}")

    async def _process_cluster(
        self,
        cluster: list[tuple],
        user_id: str,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """Crea o actualiza un UserPattern desde un cluster de episodios."""
        try:
            episodes = [ep for ep, _ in cluster]
            embeddings = [emb for _, emb in cluster]

            # Centroide del cluster
            centroid = [
                sum(emb[i] for emb in embeddings) / len(embeddings)
                for i in range(len(embeddings[0]))
            ]

            # Verificar si ya existe un patrón similar para este usuario
            pats_q = await db.execute(
                select(UserPattern)
                .where(
                    UserPattern.user_id == user_id,
                    UserPattern.area_id == area_id,
                    UserPattern.trigger_embedding.isnot(None),
                )
            )
            existing_patterns = pats_q.scalars().all()

            existing = None
            for pat in existing_patterns:
                try:
                    pat_emb = json.loads(pat.trigger_embedding)
                    if cosine_similarity(centroid, pat_emb) >= USER_CLUSTER_SIMILARITY:
                        existing = pat
                        break
                except Exception:
                    continue

            # Calcular confidence: calidad media de los episodios del cluster
            quality_scores = [ep.quality_score for ep in episodes if ep.quality_score is not None]
            mean_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.3

            # Detectar si es patrón de fallo
            failure_count = sum(1 for ep in episodes if ep.session_arc in ("abandoned", "degraded"))
            is_failure = failure_count > len(episodes) / 2

            # Representante del cluster: episodio con mayor quality_score
            best_ep = max(episodes, key=lambda ep: ep.quality_score or 0.0)

            if existing:
                # Actualizar patrón existente
                source_ids = json.loads(existing.source_episode_ids or "[]")
                for ep in episodes:
                    if ep.id not in source_ids:
                        source_ids.append(ep.id)
                existing.source_episode_ids = json.dumps(source_ids)
                existing.episode_count = len(source_ids)
                existing.confidence_score = round(mean_quality, 4)
                existing.trigger_embedding = json.dumps(centroid)
                existing.is_failure_pattern = is_failure
            else:
                # Crear nuevo patrón
                new_pattern = UserPattern(
                    user_id=user_id,
                    area_id=area_id,
                    tenant_id=tenant_id,
                    trigger_description=best_ep.situation[:300],
                    trigger_embedding=json.dumps(centroid),
                    response_description=best_ep.strategy[:300],
                    causal_mechanism=best_ep.causal_explanation,
                    confidence_score=round(mean_quality, 4),
                    episode_count=len(episodes),
                    abstraction_level=1,
                    is_failure_pattern=is_failure,
                    source_episode_ids=json.dumps([ep.id for ep in episodes]),
                    promoted_to_core=False,
                )
                db.add(new_pattern)

            await db.commit()

            # Intentar promover al CoreBrain si el patrón es suficientemente sólido
            if mean_quality >= settings.CME_CORE_PROMOTE_MIN_CONFIDENCE:
                await self._promote_to_core(
                    user_id, area_id, tenant_id,
                    best_ep.situation[:300], best_ep.strategy[:300],
                    centroid, mean_quality, len(episodes), db
                )

        except Exception as e:
            logger.warning(f"CME UserBrain: error en _process_cluster: {e}")

    async def _promote_to_core(
        self,
        user_id: str,
        area_id: str,
        tenant_id: str,
        trigger_desc: str,
        response_desc: str,
        trigger_embedding: list[float],
        confidence: float,
        episode_count: int,
        db: AsyncSession
    ) -> None:
        """
        Promueve un patrón al CoreBrain de forma anónima.
        El CoreBrain NO sabe de qué usuario proviene.
        """
        try:
            from app.cme.core_brain import core_brain
            await core_brain.receive(
                tenant_id=tenant_id,
                trigger_description=trigger_desc,
                trigger_embedding=trigger_embedding,
                response_description=response_desc,
                confidence=confidence,
                episode_count=episode_count,
                db=db,
            )
        except Exception as e:
            logger.debug(f"CME UserBrain: error promoviendo al CoreBrain: {e}")

    # ── Estadísticas del UserBrain (para el usuario) ──────────────────────────

    async def get_stats(
        self,
        user_id: str,
        area_id: str,
        db: AsyncSession
    ) -> dict:
        """Retorna estadísticas del espacio cognitivo del usuario."""
        try:
            ep_count_q = await db.execute(
                select(func.count(UserEpisode.id))
                .where(UserEpisode.user_id == user_id, UserEpisode.area_id == area_id)
            )
            pat_count_q = await db.execute(
                select(func.count(UserPattern.id))
                .where(UserPattern.user_id == user_id, UserPattern.area_id == area_id)
            )
            resolved_q = await db.execute(
                select(func.count(UserEpisode.id))
                .where(
                    UserEpisode.user_id == user_id,
                    UserEpisode.area_id == area_id,
                    UserEpisode.session_arc == "resolved",
                )
            )
            return {
                "total_episodes": ep_count_q.scalar() or 0,
                "total_patterns": pat_count_q.scalar() or 0,
                "resolved_episodes": resolved_q.scalar() or 0,
            }
        except Exception:
            return {"total_episodes": 0, "total_patterns": 0, "resolved_episodes": 0}


def _cluster_episodes(
    episodes_with_emb: list[tuple],
    threshold: float
) -> list[list[tuple]]:
    """
    Clustering greedy por similitud coseno.
    Cada episodio se asigna al primer cluster cuyo centroide supera el umbral.
    """
    clusters: list[list[tuple]] = []
    centroids: list[list[float]] = []

    for ep, emb in episodes_with_emb:
        assigned = False
        for idx, centroid in enumerate(centroids):
            if cosine_similarity(emb, centroid) >= threshold:
                clusters[idx].append((ep, emb))
                # Actualizar centroide
                n = len(clusters[idx])
                centroids[idx] = [
                    (centroid[i] * (n - 1) + emb[i]) / n
                    for i in range(len(emb))
                ]
                assigned = True
                break
        if not assigned:
            clusters.append([(ep, emb)])
            centroids.append(list(emb))

    return clusters


# Instancia global singleton
user_brain = UserBrain()
