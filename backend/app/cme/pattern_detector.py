"""
Pattern Detector — Detecta patrones recurrentes en clusters de episodios del área.
Se ejecuta cada 10 episodios nuevos como background task.
"""
import json
import logging
import httpx
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.cme import AreaEpisode, AreaPattern, AreaContradiction
from app.models.area import Area
from app.rag import cosine_similarity, get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

CLUSTER_SIMILARITY_THRESHOLD = 0.75
CONTRADICTION_TRIGGER_THRESHOLD = 0.80
CONTRADICTION_RESPONSE_THRESHOLD = 0.30
MIN_CLUSTER_SIZE = 3
FAILURE_WEIGHT_MULTIPLIER = 1.5  # Req 32.2: episodios de fallo pesan 1.5×


class PatternDetector:

    async def run_for_area(self, area_id: str, tenant_id: str, db: AsyncSession) -> None:
        """
        Analiza episodios del área y crea/actualiza patrones.
        Se ejecuta cuando episode_count_since_last_detection alcanza múltiplo de 10.
        """
        try:
            # Obtener todos los episodios con embedding del área
            eps_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.situation_embedding.isnot(None),
                    AreaEpisode.extraction_status == "completed"
                )
                .order_by(AreaEpisode.created_at)
            )
            episodes = eps_q.scalars().all()

            if len(episodes) < MIN_CLUSTER_SIZE:
                return

            # Parsear embeddings
            episodes_with_emb = []
            for ep in episodes:
                try:
                    emb = json.loads(ep.situation_embedding)
                    episodes_with_emb.append((ep, emb))
                except Exception:
                    continue

            if len(episodes_with_emb) < MIN_CLUSTER_SIZE:
                return

            # Agrupar en clusters
            clusters = self.cluster_episodes(episodes_with_emb, CLUSTER_SIMILARITY_THRESHOLD)

            for cluster in clusters:
                await self._process_cluster(cluster, area_id, tenant_id, db)

            # Actualizar timestamp de última detección y resetear contador
            area_q = await db.execute(select(Area).where(Area.id == area_id))
            area = area_q.scalar_one_or_none()
            if area:
                area.last_pattern_detection_at = datetime.now(timezone.utc)
                area.episode_count_since_last_detection = 0
                await db.commit()

            logger.info(f"CME PatternDetector: {len(clusters)} clusters procesados para área {area_id}")

        except Exception as e:
            logger.error(f"CME PatternDetector: error en run_for_area {area_id}: {e}")

    def cluster_episodes(
        self,
        episodes_with_emb: list[tuple],
        threshold: float = CLUSTER_SIMILARITY_THRESHOLD
    ) -> list[list[tuple]]:
        """
        Agrupa episodios por cosine similarity ≥ threshold en situation_embedding.
        Retorna clusters de ≥ MIN_CLUSTER_SIZE episodios.
        Usa algoritmo greedy de clustering (sin dependencias externas).
        """
        if not episodes_with_emb:
            return []

        assigned = [False] * len(episodes_with_emb)
        clusters = []

        for i, (ep_i, emb_i) in enumerate(episodes_with_emb):
            if assigned[i]:
                continue

            cluster = [(ep_i, emb_i)]
            assigned[i] = True

            for j, (ep_j, emb_j) in enumerate(episodes_with_emb):
                if assigned[j] or i == j:
                    continue
                sim = cosine_similarity(emb_i, emb_j)
                if sim >= threshold:
                    cluster.append((ep_j, emb_j))
                    assigned[j] = True

            if len(cluster) >= MIN_CLUSTER_SIZE:
                clusters.append(cluster)

        return clusters

    def compute_confidence(
        self,
        quality_scores: list[float],
        diversity_score: float
    ) -> float:
        """
        confidence = (mean_quality × 0.6) + (diversity_score × 0.4)
        Default 0.3 si no hay quality scores disponibles.
        """
        if not quality_scores:
            return 0.3
        mean_quality = sum(quality_scores) / len(quality_scores)
        confidence = (mean_quality * 0.6) + (diversity_score * 0.4)
        return round(max(0.0, min(1.0, confidence)), 4)

    def compute_diversity(self, user_ids: list[str]) -> float:
        """
        diversity = min(distinct_users / total_episodes, 1.0)
        Un patrón validado por 5 usuarios distintos vale más que 10 del mismo usuario.
        """
        if not user_ids:
            return 0.0
        distinct = len(set(user_ids))
        total = len(user_ids)
        return round(min(distinct / total, 1.0), 4)

    async def _process_cluster(
        self,
        cluster: list[tuple],
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """Crea o actualiza un patrón para un cluster de episodios."""
        episodes = [ep for ep, _ in cluster]
        embeddings = [emb for _, emb in cluster]

        # Obtener user_ids de las sesiones (via session_id → ChatSession.user_id)
        from app.models.chat import ChatSession
        user_ids = []
        for ep in episodes:
            try:
                sess_q = await db.execute(
                    select(ChatSession.user_id).where(ChatSession.id == ep.session_id)
                )
                uid = sess_q.scalar_one_or_none()
                if uid:
                    user_ids.append(uid)
            except Exception:
                pass

        # Calcular métricas del cluster
        # Ponderar episodios de fallo 1.5× (Req 32.2)
        weighted_quality_scores = []
        for ep in episodes:
            if ep.quality_score is not None:
                weight = FAILURE_WEIGHT_MULTIPLIER if ep.session_arc in ("abandoned", "degraded") else 1.0
                weighted_quality_scores.extend([ep.quality_score] * int(weight * 10))

        diversity_score = self.compute_diversity(user_ids)
        confidence_score = self.compute_confidence(
            [qs / 10 for qs in weighted_quality_scores] if weighted_quality_scores else [],
            diversity_score
        )

        # Calcular embedding promedio del cluster para el trigger
        avg_embedding = self._average_embeddings(embeddings)

        # Buscar patrón existente similar (cosine ≥ 0.75 con trigger_embedding)
        existing_pattern = await self._find_similar_pattern(avg_embedding, area_id, db)

        # Sintetizar causal_mechanism si todos los episodios del cluster tienen causal_explanation
        causal_mechanism = None
        causal_explanations = [ep.causal_explanation for ep in episodes if ep.causal_explanation]
        if len(causal_explanations) == len(episodes) and causal_explanations:
            causal_mechanism = await self._synthesize_causal_mechanism(causal_explanations)

        # Determinar si es un failure pattern
        failure_count = sum(1 for ep in episodes if ep.session_arc in ("abandoned", "degraded"))
        is_failure_pattern = failure_count > len(episodes) / 2

        # Generar descripciones via LLM
        trigger_desc, response_desc = await self._generate_pattern_descriptions(episodes)
        if not trigger_desc:
            return  # No se pudo generar descripción

        # Generar embedding del response para detección de contradicciones
        response_embedding = await get_embedding(response_desc) if response_desc else None

        source_episode_ids = json.dumps([ep.id for ep in episodes])

        if existing_pattern:
            # Actualizar patrón existente
            existing_pattern.confidence_score = confidence_score
            existing_pattern.diversity_score = diversity_score
            existing_pattern.distinct_user_count = len(set(user_ids))
            existing_pattern.episode_count = len(episodes)
            existing_pattern.source_episode_ids = source_episode_ids
            if causal_mechanism:
                existing_pattern.causal_mechanism = causal_mechanism
            if response_embedding:
                existing_pattern.response_embedding = json.dumps(response_embedding)
            # Auto-aprobación si está configurado
            await self._maybe_auto_approve(existing_pattern, len(set(user_ids)))
            await db.commit()
            pattern = existing_pattern
        else:
            # Determinar is_approved según modo de configuración
            auto_approved = await self._should_auto_approve(confidence_score, len(set(user_ids)))
            # Crear nuevo patrón
            pattern = AreaPattern(
                area_id=area_id,
                tenant_id=tenant_id,
                trigger_description=trigger_desc,
                trigger_embedding=json.dumps(avg_embedding) if avg_embedding else None,
                response_description=response_desc or "",
                response_embedding=json.dumps(response_embedding) if response_embedding else None,
                causal_mechanism=causal_mechanism,
                confidence_score=confidence_score,
                diversity_score=diversity_score,
                episode_count=len(episodes),
                distinct_user_count=len(set(user_ids)),
                abstraction_level=1,
                is_approved=auto_approved,
                is_failure_pattern=is_failure_pattern,
                source_episode_ids=source_episode_ids,
            )
            db.add(pattern)
            await db.commit()
            await db.refresh(pattern)

        # Detectar contradicciones con patrones aprobados existentes
        await self._detect_contradictions(pattern, area_id, db)

        # Promover al Global Brain si confidence ≥ threshold y is_approved=True
        if pattern.confidence_score >= CLUSTER_SIMILARITY_THRESHOLD and pattern.is_approved:
            try:
                from app.cme.global_brain import global_brain
                await global_brain.promote_pattern(pattern, tenant_id, db)
            except ImportError:
                pass  # GlobalBrain aún no implementado
            except Exception as e:
                logger.warning(f"CME PatternDetector: error promoviendo al Global Brain: {e}")

    async def _detect_contradictions(
        self,
        new_pattern: "AreaPattern",
        area_id: str,
        db: AsyncSession
    ) -> None:
        """
        Detecta contradicciones: trigger_sim ≥ 0.80 Y response_sim ≤ 0.30.
        Solo compara contra patrones aprobados existentes.
        """
        if not new_pattern.trigger_embedding:
            return

        try:
            new_trigger_emb = json.loads(new_pattern.trigger_embedding)
            new_response_emb = json.loads(new_pattern.response_embedding) if new_pattern.response_embedding else None
        except Exception:
            return

        # Obtener patrones aprobados del área (excluyendo el nuevo)
        approved_q = await db.execute(
            select(AreaPattern)
            .where(
                AreaPattern.area_id == area_id,
                AreaPattern.is_approved == True,
                AreaPattern.id != new_pattern.id,
                AreaPattern.trigger_embedding.isnot(None)
            )
        )
        approved_patterns = approved_q.scalars().all()

        for existing in approved_patterns:
            try:
                existing_trigger_emb = json.loads(existing.trigger_embedding)
                trigger_sim = cosine_similarity(new_trigger_emb, existing_trigger_emb)

                if trigger_sim < CONTRADICTION_TRIGGER_THRESHOLD:
                    continue

                # Situaciones similares — verificar si las respuestas son opuestas
                if not new_response_emb or not existing.response_embedding:
                    continue

                existing_response_emb = json.loads(existing.response_embedding)
                response_sim = cosine_similarity(new_response_emb, existing_response_emb)

                if response_sim <= CONTRADICTION_RESPONSE_THRESHOLD:
                    # Contradicción detectada — verificar si ya existe
                    existing_contradiction_q = await db.execute(
                        select(AreaContradiction)
                        .where(
                            AreaContradiction.area_id == area_id,
                            AreaContradiction.status == "pending",
                            (
                                (AreaContradiction.pattern_a_id == new_pattern.id) |
                                (AreaContradiction.pattern_b_id == new_pattern.id)
                            )
                        )
                    )
                    if existing_contradiction_q.scalar_one_or_none():
                        continue

                    contradiction = AreaContradiction(
                        area_id=area_id,
                        pattern_a_id=new_pattern.id,
                        pattern_b_id=existing.id,
                        description=(
                            f"Situaciones similares (sim={trigger_sim:.2f}) pero respuestas opuestas "
                            f"(sim={response_sim:.2f}). Patrón A: '{new_pattern.trigger_description[:100]}' "
                            f"vs Patrón B: '{existing.trigger_description[:100]}'"
                        ),
                        status="pending",
                    )
                    db.add(contradiction)
                    await db.commit()
                    logger.warning(
                        f"CME PatternDetector: contradicción detectada entre patrones "
                        f"{new_pattern.id} y {existing.id}"
                    )
            except Exception as e:
                logger.debug(f"CME PatternDetector: error comparando patrones: {e}")

    async def _find_similar_pattern(
        self,
        embedding: list[float],
        area_id: str,
        db: AsyncSession
    ) -> "AreaPattern | None":
        """Busca un patrón existente con trigger_embedding similar (cosine ≥ 0.75)."""
        if not embedding:
            return None

        patterns_q = await db.execute(
            select(AreaPattern)
            .where(
                AreaPattern.area_id == area_id,
                AreaPattern.trigger_embedding.isnot(None)
            )
        )
        patterns = patterns_q.scalars().all()

        best_match = None
        best_sim = 0.0

        for pattern in patterns:
            try:
                pattern_emb = json.loads(pattern.trigger_embedding)
                sim = cosine_similarity(embedding, pattern_emb)
                if sim >= CLUSTER_SIMILARITY_THRESHOLD and sim > best_sim:
                    best_sim = sim
                    best_match = pattern
            except Exception:
                continue

        return best_match

    def _average_embeddings(self, embeddings: list[list[float]]) -> list[float] | None:
        """Calcula el embedding promedio de un cluster."""
        if not embeddings:
            return None
        dim = len(embeddings[0])
        avg = [0.0] * dim
        for emb in embeddings:
            for i, v in enumerate(emb):
                avg[i] += v
        n = len(embeddings)
        return [v / n for v in avg]

    async def _generate_pattern_descriptions(
        self,
        episodes: list
    ) -> tuple[str | None, str | None]:
        """Genera trigger_description y response_description via LLM."""
        try:
            sample = "\n".join(
                f"- Situación: {ep.situation[:150]} | Estrategia: {ep.strategy[:150]}"
                for ep in episodes[:5]
            )
            prompt = (
                f"Analiza estos {len(episodes)} episodios similares y genera en JSON:\n"
                f'{{"trigger": "descripción del disparador común (≤200 chars)", '
                f'"response": "descripción de la respuesta efectiva común (≤200 chars)"}}\n\n'
                f"Episodios:\n{sample}\n\nSolo JSON, sin texto adicional."
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
                if res.status_code != 200:
                    return None, None

                content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                content = content.strip()
                if "```" in content:
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]

                data = json.loads(content.strip())
                return data.get("trigger", "")[:200], data.get("response", "")[:200]
        except Exception as e:
            logger.debug(f"CME PatternDetector: error generando descripciones: {e}")
            # Fallback: usar el primer episodio como descripción
            if episodes:
                return episodes[0].situation[:200], episodes[0].strategy[:200]
            return None, None

    async def _should_auto_approve(self, confidence_score: float, distinct_users: int) -> bool:
        """Determina si un patrón debe auto-aprobarse según la configuración."""
        from app.config import settings
        if settings.CME_APPROVAL_MODE == "auto":
            return confidence_score >= settings.CME_AUTO_APPROVE_THRESHOLD
        return False

    async def _maybe_auto_approve(self, pattern: "AreaPattern", distinct_users: int) -> None:
        """Auto-aprueba un patrón existente si cumple los criterios y no está ya aprobado."""
        if not pattern.is_approved:
            from app.config import settings
            if settings.CME_APPROVAL_MODE == "auto":
                if pattern.confidence_score >= settings.CME_AUTO_APPROVE_THRESHOLD:
                    pattern.is_approved = True
                    logger.info(f"CME PatternDetector: patrón {pattern.id[:8]} auto-aprobado (confidence={pattern.confidence_score:.2f})")

    async def _synthesize_causal_mechanism(self, explanations: list[str]) -> str | None:
        """Sintetiza el mecanismo causal común de un cluster de episodios."""
        try:
            sample = "\n".join(f"- {e[:150]}" for e in explanations[:5])
            prompt = (
                f"¿Cuál es el mecanismo causal común en estas explicaciones? "
                f"Responde en 1-2 oraciones (≤300 chars):\n{sample}"
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


# Instancia global singleton
pattern_detector = PatternDetector()
