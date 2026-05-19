
"""
Nocturnal Consolidation — Mantenimiento nocturno del Area Brain.

Ejecuta secuencialmente para cada área del tenant:
1. ForgettingCurve.apply_decay_for_area()
2. Fusionar patrones redundantes (cosine >= 0.90)
3. Recomputar confidence_score y diversity_score
4. Podar concept_edges con weight < 0.5 sin refuerzo en 90+ días
5. AbstractionEngine.evaluate_promotion()
6. AbstractionEngine.cross_domain_cluster()
7. SynthesisReporter.generate_report() si es día de reporte semanal
8. Regenerar self_description de AgentIdentity si está activa
9. Generar curiosity questions para top 5 knowledge gaps
10. Detectar temporal chains

Es interruptible: verifica flag _interrupted por área antes de cada paso.
"""
import json
import logging
import time
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from app.models.cme import (
    AreaPattern,
    AreaEpisode,
    AreaConceptEdge,
    AreaKnowledgeGap,
    AgentIdentity,
    CuriosityQueue,
    TemporalChain,
    ConsolidationLog,
)
from app.models.area import Area
from app.rag import cosine_similarity
from app.config import settings

logger = logging.getLogger(__name__)

MERGE_SIMILARITY_THRESHOLD = 0.90
EDGE_PRUNE_WEIGHT_THRESHOLD = 0.5
EDGE_PRUNE_DAYS = 90
TEMPORAL_CHAIN_MIN_SIMILARITY = 0.65
TEMPORAL_CHAIN_MAX_DAYS = 30
REPORT_DAY_OF_WEEK = 0  # Lunes (0=Monday en Python weekday())


class NocturnalConsolidation:

    def __init__(self):
        # Flags de interrupción por area_id
        self._interrupted: dict[str, bool] = {}

    def interrupt(self, area_id: str) -> None:
        """Señala interrupción para un área específica."""
        self._interrupted[area_id] = True
        logger.info(f"CME NocturnalConsolidation: interrupción solicitada para área {area_id}")

    def clear_interrupt(self, area_id: str) -> None:
        """Limpia el flag de interrupción para un área."""
        self._interrupted.pop(area_id, None)

    def _is_interrupted(self, area_id: str) -> bool:
        """Verifica si el área tiene una interrupción pendiente."""
        return self._interrupted.get(area_id, False)

    async def run_for_tenant(
        self,
        tenant_id: str,
        db: AsyncSession
    ) -> ConsolidationLog:
        """
        Ejecuta la consolidación nocturna para todas las áreas del tenant.
        Genera ConsolidationLog con métricas agregadas.
        """
        start_time = time.monotonic()
        total_patterns_merged = 0
        total_edges_pruned = 0
        total_episodes_reweighted = 0

        try:
            # Obtener todas las áreas del tenant
            areas_q = await db.execute(
                select(Area).where(Area.tenant_id == tenant_id)
            )
            areas = areas_q.scalars().all()

            for area in areas:
                area_id = area.id
                self.clear_interrupt(area_id)

                try:
                    metrics = await self._run_for_area(area, tenant_id, db)
                    total_patterns_merged += metrics.get("patterns_merged", 0)
                    total_edges_pruned += metrics.get("edges_pruned", 0)
                    total_episodes_reweighted += metrics.get("episodes_reweighted", 0)
                except Exception as e:
                    logger.error(
                        f"CME NocturnalConsolidation: error en área {area_id}: {e}"
                    )
                    continue

            # ── Universal Brain: evaluar patrones globales maduros ────────────
            try:
                from app.config import settings as _cfg
                if _cfg.CME_ENABLE_UNIVERSAL_BRAIN:
                    await self._evaluate_global_patterns_for_universal(tenant_id, db)
            except Exception as e:
                logger.warning(f"CME NocturnalConsolidation: error en Universal Brain evaluation: {e}")

        except Exception as e:
            logger.error(f"CME NocturnalConsolidation: error en run_for_tenant: {e}")

        duration = time.monotonic() - start_time

        # Crear ConsolidationLog
        log = ConsolidationLog(
            tenant_id=tenant_id,
            area_id=None,  # log a nivel tenant
            patterns_merged=total_patterns_merged,
            edges_pruned=total_edges_pruned,
            episodes_reweighted=total_episodes_reweighted,
            duration_seconds=round(duration, 2),
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)

        logger.info(
            f"CME NocturnalConsolidation: consolidación completada para tenant {tenant_id} "
            f"en {duration:.1f}s — {total_patterns_merged} patrones fusionados, "
            f"{total_edges_pruned} aristas podadas, {total_episodes_reweighted} episodios reweighted"
        )
        return log

    async def _run_for_area(
        self,
        area: Area,
        tenant_id: str,
        db: AsyncSession
    ) -> dict:
        """
        Ejecuta los 10 pasos de consolidación para un área.
        Verifica flag de interrupción antes de cada paso.
        Retorna métricas del área.
        """
        area_id = area.id
        metrics = {
            "patterns_merged": 0,
            "edges_pruned": 0,
            "episodes_reweighted": 0,
        }

        # ── Paso 1: Forgetting Curve ──────────────────────────────────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            from app.cme.forgetting_curve import forgetting_curve
            count = await forgetting_curve.apply_decay_for_area(
                area_id=area_id,
                lambda_rate=area.cme_lambda_rate,
                db=db,
            )
            metrics["episodes_reweighted"] = count
            logger.debug(f"CME NocturnalConsolidation [{area_id}] paso 1: {count} episodios reweighted")
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 1 error: {e}")

        # ── Paso 2: Fusionar patrones redundantes (cosine >= 0.90) ────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            merged = await self._merge_redundant_patterns(area_id, db)
            metrics["patterns_merged"] = merged
            logger.debug(f"CME NocturnalConsolidation [{area_id}] paso 2: {merged} patrones fusionados")
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 2 error: {e}")

        # ── Paso 3: Recomputar confidence_score y diversity_score ─────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            await self._recompute_pattern_scores(area_id, db)
            logger.debug(f"CME NocturnalConsolidation [{area_id}] paso 3: scores recomputados")
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 3 error: {e}")

        # ── Paso 4: Podar concept_edges ───────────────────────────────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            pruned = await self._prune_concept_edges(area_id, db)
            metrics["edges_pruned"] = pruned
            logger.debug(f"CME NocturnalConsolidation [{area_id}] paso 4: {pruned} aristas podadas")
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 4 error: {e}")

        # ── Paso 5: AbstractionEngine.evaluate_promotion() ───────────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            from app.cme.abstraction_engine import abstraction_engine
            principles = await abstraction_engine.evaluate_promotion(area_id, tenant_id, db)
            logger.debug(
                f"CME NocturnalConsolidation [{area_id}] paso 5: {len(principles)} principios creados"
            )
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 5 error: {e}")

        # ── Paso 6: AbstractionEngine.cross_domain_cluster() ─────────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            from app.cme.abstraction_engine import abstraction_engine
            insights = await abstraction_engine.cross_domain_cluster(area_id, db)
            logger.debug(
                f"CME NocturnalConsolidation [{area_id}] paso 6: {len(insights)} insights cross-domain"
            )
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 6 error: {e}")

        # ── Paso 7: SynthesisReporter (si es día de reporte semanal) ─────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            now = datetime.now(timezone.utc)
            if now.weekday() == REPORT_DAY_OF_WEEK:
                from app.cme.synthesis_reporter import synthesis_reporter
                report = await synthesis_reporter.generate_report(area_id, tenant_id, db)
                logger.debug(
                    f"CME NocturnalConsolidation [{area_id}] paso 7: reporte generado {report.id}"
                )
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 7 error: {e}")

        # ── Paso 8: Regenerar self_description de AgentIdentity ──────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            from app.config import settings as _cfg
            if _cfg.CME_ENABLE_AGENT_IDENTITY:
                from app.cme.agent_identity import agent_identity_module
                await agent_identity_module.regenerate_self_description(area_id, db)
            else:
                await self._regenerate_agent_identity(area_id, tenant_id, db)
            logger.debug(f"CME NocturnalConsolidation [{area_id}] paso 8: identidad regenerada")
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 8 error: {e}")

        # ── Paso 9: Generar curiosity questions para top 5 knowledge gaps ─────
        if self._is_interrupted(area_id):
            return metrics
        try:
            from app.config import settings as _cfg
            if _cfg.CME_ENABLE_GENERATIVE_CURIOSITY:
                from app.cme.generative_curiosity import generative_curiosity
                questions = await generative_curiosity.generate_questions_for_gaps(area_id, db)
                logger.debug(
                    f"CME NocturnalConsolidation [{area_id}] paso 9: "
                    f"{len(questions)} curiosity questions generadas"
                )
            else:
                await self._generate_curiosity_questions(area_id, db)
                logger.debug(f"CME NocturnalConsolidation [{area_id}] paso 9: curiosity questions generadas")
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 9 error: {e}")

        # ── Paso 10: Detectar temporal chains ────────────────────────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            from app.config import settings as _cfg
            if _cfg.CME_ENABLE_TEMPORAL_NARRATIVE:
                from app.cme.temporal_narrative import temporal_narrative
                chains = await temporal_narrative.detect_chains(area_id, db)
                logger.debug(
                    f"CME NocturnalConsolidation [{area_id}] paso 10: "
                    f"{len(chains)} temporal chains detectadas"
                )
            else:
                chains_count = await self._detect_temporal_chains(area_id, db)
                logger.debug(
                    f"CME NocturnalConsolidation [{area_id}] paso 10: "
                    f"{chains_count} temporal chains detectadas"
                )
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 10 error: {e}")

        # ── Paso 11: Detectar cross-domain insights ───────────────────────────
        if self._is_interrupted(area_id):
            return metrics
        try:
            from app.config import settings as _cfg
            if _cfg.CME_ENABLE_CROSS_DOMAIN_INSIGHTS:
                from app.cme.cross_domain_insight import cross_domain_insights
                insights = await cross_domain_insights.detect_connections(area_id, db)
                logger.debug(
                    f"CME NocturnalConsolidation [{area_id}] paso 11: "
                    f"{len(insights)} cross-domain insights detectados"
                )
        except Exception as e:
            logger.warning(f"CME NocturnalConsolidation [{area_id}] paso 11 error: {e}")

        return metrics    async def _evaluate_global_patterns_for_universal(
        self,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """
        Evalúa patrones globales maduros del tenant para promoción al Universal Brain.
        Solo evalúa patrones con confidence_score ≥ 0.85 y ≥ 3 áreas fuente. (Req 36.2)
        """
        try:
            from app.models.cme import GlobalPattern
            from app.cme.global_brain import universal_brain

            # Obtener patrones globales candidatos del tenant
            candidates_q = await db.execute(
                select(GlobalPattern)
                .where(
                    GlobalPattern.tenant_id == tenant_id,
                    GlobalPattern.confidence_score >= 0.85,
                )
            )
            candidates = candidates_q.scalars().all()

            promoted_count = 0
            for gp in candidates:
                try:
                    promoted = await universal_brain.evaluate_for_promotion(gp, db)
                    if promoted:
                        promoted_count += 1
                except Exception as e:
                    logger.debug(
                        f"CME NocturnalConsolidation: error evaluando global_pattern {gp.id} "
                        f"para Universal Brain: {e}"
                    )
                    continue

            if promoted_count > 0:
                logger.info(
                    f"CME NocturnalConsolidation: {promoted_count} patrones globales "
                    f"enviados a Universal Brain (pending_approval) para tenant {tenant_id}"
                )

        except Exception as e:
            logger.error(
                f"CME NocturnalConsolidation: error en _evaluate_global_patterns_for_universal: {e}"
            )

    async def _merge_redundant_patterns(self, area_id: str, db: AsyncSession) -> int:        """
        Fusiona patrones aprobados con cosine similarity >= 0.90.
        Conserva el de mayor confidence_score, hace union de source_episode_ids,
        suma distinct_user_count, elimina el duplicado.
        Retorna número de patrones eliminados (fusionados).
        """
        merged_count = 0

        patterns_q = await db.execute(
            select(AreaPattern)
            .where(
                AreaPattern.area_id == area_id,
                AreaPattern.is_approved == True,
                AreaPattern.trigger_embedding.isnot(None),
            )
        )
        patterns = patterns_q.scalars().all()

        if len(patterns) < 2:
            return 0

        # Parsear embeddings
        patterns_with_emb = []
        for p in patterns:
            try:
                emb = json.loads(p.trigger_embedding)
                patterns_with_emb.append((p, emb))
            except Exception:
                continue

        # Encontrar pares a fusionar
        to_delete = set()

        for i in range(len(patterns_with_emb)):
            if patterns_with_emb[i][0].id in to_delete:
                continue
            p_i, emb_i = patterns_with_emb[i]

            for j in range(i + 1, len(patterns_with_emb)):
                if patterns_with_emb[j][0].id in to_delete:
                    continue
                p_j, emb_j = patterns_with_emb[j]

                sim = cosine_similarity(emb_i, emb_j)
                if sim >= MERGE_SIMILARITY_THRESHOLD:
                    # Determinar cuál conservar (mayor confidence_score)
                    if p_i.confidence_score >= p_j.confidence_score:
                        winner, loser = p_i, p_j
                    else:
                        winner, loser = p_j, p_i

                    # Union de source_episode_ids
                    try:
                        winner_ids = set(json.loads(winner.source_episode_ids or "[]"))
                        loser_ids = set(json.loads(loser.source_episode_ids or "[]"))
                        merged_ids = list(winner_ids | loser_ids)
                        winner.source_episode_ids = json.dumps(merged_ids)
                    except Exception:
                        pass

                    # Suma de distinct_user_count
                    winner.distinct_user_count = (
                        winner.distinct_user_count + loser.distinct_user_count
                    )

                    # Actualizar episode_count
                    winner.episode_count = max(
                        winner.episode_count,
                        winner.episode_count + loser.episode_count,
                    )

                    to_delete.add(loser.id)
                    merged_count += 1

        # Eliminar patrones duplicados
        for pattern_id in to_delete:
            pattern_q = await db.execute(
                select(AreaPattern).where(AreaPattern.id == pattern_id)
            )
            pattern = pattern_q.scalar_one_or_none()
            if pattern:
                await db.delete(pattern)

        if merged_count > 0:
            await db.commit()

        return merged_count

    async def _recompute_pattern_scores(self, area_id: str, db: AsyncSession) -> None:
        """
        Recomputa confidence_score y diversity_score de todos los patrones del área
        desde cero con datos actuales de episodios.
        """
        patterns_q = await db.execute(
            select(AreaPattern)
            .where(AreaPattern.area_id == area_id)
        )
        patterns = patterns_q.scalars().all()

        for pattern in patterns:
            try:
                source_ids = json.loads(pattern.source_episode_ids or "[]")
                if not source_ids:
                    continue

                # Obtener episodios fuente
                episodes_q = await db.execute(
                    select(AreaEpisode)
                    .where(AreaEpisode.id.in_(source_ids))
                )
                episodes = episodes_q.scalars().all()

                if not episodes:
                    continue

                # Obtener user_ids únicos via sesiones
                from app.models.chat import ChatSession
                user_ids = []
                for ep in episodes:
                    sess_q = await db.execute(
                        select(ChatSession.user_id).where(ChatSession.id == ep.session_id)
                    )
                    uid = sess_q.scalar_one_or_none()
                    if uid:
                        user_ids.append(uid)

                # Recomputar diversity_score
                if user_ids:
                    distinct = len(set(user_ids))
                    total = len(user_ids)
                    pattern.diversity_score = round(min(distinct / total, 1.0), 4)

                # Recomputar confidence_score
                quality_scores = [
                    ep.quality_score for ep in episodes if ep.quality_score is not None
                ]
                if quality_scores:
                    mean_quality = sum(quality_scores) / len(quality_scores)
                    pattern.confidence_score = round(
                        (mean_quality * 0.6) + (pattern.diversity_score * 0.4), 4
                    )

            except Exception as e:
                logger.debug(f"CME NocturnalConsolidation: error recomputando patrón {pattern.id}: {e}")

        await db.commit()

    async def _prune_concept_edges(self, area_id: str, db: AsyncSession) -> int:
        """
        Elimina aristas del concept graph con weight < 0.5 sin refuerzo en 90+ días.
        Retorna número de aristas eliminadas.
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=EDGE_PRUNE_DAYS)

        edges_q = await db.execute(
            select(AreaConceptEdge)
            .where(
                AreaConceptEdge.area_id == area_id,
                AreaConceptEdge.weight < EDGE_PRUNE_WEIGHT_THRESHOLD,
                AreaConceptEdge.last_reinforced_at < cutoff_date,
            )
        )
        edges_to_prune = edges_q.scalars().all()
        count = len(edges_to_prune)

        for edge in edges_to_prune:
            await db.delete(edge)

        if count > 0:
            await db.commit()

        return count

    async def _regenerate_agent_identity(
        self,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """
        Regenera self_description de AgentIdentity si está activa. Req 28.2
        """
        identity_q = await db.execute(
            select(AgentIdentity)
            .where(
                AgentIdentity.area_id == area_id,
                AgentIdentity.is_enabled == True,
            )
        )
        identity = identity_q.scalar_one_or_none()

        if not identity:
            return

        try:
            # Obtener estadísticas del área para la descripción
            total_patterns_q = await db.execute(
                select(func.count(AreaPattern.id))
                .where(
                    AreaPattern.area_id == area_id,
                    AreaPattern.is_approved == True,
                )
            )
            total_patterns = total_patterns_q.scalar() or 0

            total_episodes_q = await db.execute(
                select(func.count(AreaEpisode.id))
                .where(AreaEpisode.area_id == area_id)
            )
            total_episodes = total_episodes_q.scalar() or 0

            # Actualizar contadores
            identity.total_episodes = total_episodes

            # Generar nueva self_description via LLM
            core_values = json.loads(identity.core_values or "[]")
            values_text = ", ".join(core_values[:3]) if core_values else "aprendizaje continuo"

            birth_date_str = identity.birth_date.strftime("%d/%m/%Y") if identity.birth_date else "desconocida"

            prompt = (
                f"Eres {identity.name}, un agente cognitivo organizacional. "
                f"Genera una auto-descripción en primera persona (≤200 chars) basada en:\n"
                f"- Fecha de nacimiento: {birth_date_str}\n"
                f"- Sesiones procesadas: {identity.total_sessions}\n"
                f"- Episodios aprendidos: {total_episodes}\n"
                f"- Patrones aprobados: {total_patterns}\n"
                f"- Valores core: {values_text}\n\n"
                f"Solo la auto-descripción, sin texto adicional."
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
                        identity.self_description = content[:200]

            await db.commit()

        except Exception as e:
            logger.debug(f"CME NocturnalConsolidation: error regenerando identidad {area_id}: {e}")

    async def _generate_curiosity_questions(
        self,
        area_id: str,
        db: AsyncSession
    ) -> None:
        """
        Genera curiosity questions para los top 5 knowledge gaps del área. Req 33.1
        Solo genera si el gap no tiene ya una pregunta pendiente.
        """
        top_gaps_q = await db.execute(
            select(AreaKnowledgeGap)
            .where(
                AreaKnowledgeGap.area_id == area_id,
                AreaKnowledgeGap.status == "pending",
            )
            .order_by(AreaKnowledgeGap.occurrence_count.desc())
            .limit(5)
        )
        top_gaps = top_gaps_q.scalars().all()

        for gap in top_gaps:
            try:
                # Verificar si ya existe pregunta pendiente para este gap
                existing_q = await db.execute(
                    select(CuriosityQueue)
                    .where(
                        CuriosityQueue.gap_id == gap.id,
                        CuriosityQueue.status == "pending",
                    )
                )
                if existing_q.scalar_one_or_none():
                    continue

                # Generar pregunta via LLM
                question_text = await self._generate_curiosity_question(gap)
                if not question_text:
                    continue

                curiosity = CuriosityQueue(
                    area_id=area_id,
                    gap_id=gap.id,
                    question_text=question_text,
                    status="pending",
                )
                db.add(curiosity)

            except Exception as e:
                logger.debug(
                    f"CME NocturnalConsolidation: error generando curiosity para gap {gap.id}: {e}"
                )

        await db.commit()

    async def _generate_curiosity_question(self, gap: AreaKnowledgeGap) -> str | None:
        """Genera una pregunta de curiosidad para un knowledge gap via LLM."""
        fallback = f"¿Cómo podemos abordar mejor el tema: '{gap.topic_description[:150]}'?"

        try:
            prompt = (
                f"El sistema detectó una brecha de conocimiento recurrente "
                f"({gap.occurrence_count} veces): '{gap.topic_description[:200]}'\n\n"
                f"Genera una pregunta de investigación concreta (≤200 chars) que ayude "
                f"a cerrar esta brecha. Solo la pregunta, sin texto adicional."
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
                        return content[:200]

        except Exception:
            pass

        return fallback

    async def _detect_temporal_chains(self, area_id: str, db: AsyncSession) -> int:
        """
        Detecta cadenas causales temporales entre episodios del área. Req 34.2
        Busca pares de episodios con:
        - Similitud coseno >= 0.65 en situation_embedding
        - Diferencia temporal entre 1 y 30 días
        - No existe ya una TemporalChain para ese par
        Retorna número de cadenas creadas.
        """
        chains_created = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)

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
                .limit(50)  # limitar para eficiencia
            )
            episodes = episodes_q.scalars().all()

            if len(episodes) < 2:
                return 0

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
                    if delta_days < 1 or delta_days > TEMPORAL_CHAIN_MAX_DAYS:
                        continue

                    # Verificar similitud
                    sim = cosine_similarity(emb_a, emb_b)
                    if sim < TEMPORAL_CHAIN_MIN_SIMILARITY:
                        continue

                    # Verificar que no existe ya esta cadena
                    existing_q = await db.execute(
                        select(TemporalChain)
                        .where(
                            TemporalChain.area_id == area_id,
                            (
                                (TemporalChain.episode_a_id == ep_a.id) &
                                (TemporalChain.episode_b_id == ep_b.id)
                            ) | (
                                (TemporalChain.episode_a_id == ep_b.id) &
                                (TemporalChain.episode_b_id == ep_a.id)
                            ),
                        )
                    )
                    if existing_q.scalar_one_or_none():
                        continue

                    # Generar descripción del vínculo causal
                    causal_desc = await self._generate_causal_link(ep_a, ep_b, delta_days)

                    chain = TemporalChain(
                        area_id=area_id,
                        episode_a_id=ep_a.id,
                        episode_b_id=ep_b.id,
                        time_delta_days=round(delta_days, 2),
                        causal_link_description=causal_desc,
                        confidence=round(sim, 4),
                    )
                    db.add(chain)
                    chains_created += 1

            if chains_created > 0:
                await db.commit()

        except Exception as e:
            logger.error(f"CME NocturnalConsolidation: error detectando temporal chains: {e}")

        return chains_created

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

        except Exception:
            pass

        return fallback


# Instancia global singleton
nocturnal_consolidation = NocturnalConsolidation()
