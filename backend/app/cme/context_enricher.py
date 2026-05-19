"""
Context Enricher — Enriquece el system prompt con memoria cognitiva relevante.
Se ejecuta antes de cada llamada al LLM. Debe completarse en < 800ms.
Retorna None silenciosamente si falla (backward-compatible con memory.py + rag.py).
"""
import json
import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import (
    AreaEpisode, AreaPattern, AreaMethodology, AreaConceptEdge,
    AreaContradiction, UserCognitiveProfile
)
from app.rag import cosine_similarity, get_embedding
from app.cme.forgetting_curve import forgetting_curve
from app.cme.global_brain import global_brain, universal_brain
from app.config import settings

logger = logging.getLogger(__name__)

# Umbrales de similitud
EPISODE_MIN_RELEVANCE = 0.60    # cosine × temporal_weight
PATTERN_MIN_SIMILARITY = 0.65
METHODOLOGY_MIN_SIMILARITY = 0.65
CONCEPT_EDGE_STRONG_LINK = 10.0  # weight > 10 = strong concept link

# Límites de inclusión
MAX_PRINCIPLES = 1
MAX_PATTERNS = 2
MAX_EPISODES = 3
MAX_METHODOLOGIES = 2
MAX_GLOBAL_PATTERNS = 2
MAX_GLOBAL_METHODOLOGIES = 1


async def _none_coroutine():
    """Coroutine auxiliar que retorna None (reemplaza asyncio.coroutine deprecado)."""
    return None


class ContextEnricher:

    async def enrich(
        self,
        query: str,
        area_id: str,
        tenant_id: str,
        working_memory,
        db: AsyncSession,
        user_id: str | None = None
    ) -> str | None:
        """
        Construye el payload de enriquecimiento cognitivo para el system prompt.
        Retorna None si no hay contenido relevante o si falla.
        Debe completarse en < 800ms bajo condiciones normales.
        """
        try:
            # Generar embedding del query
            query_embedding = await get_embedding(query)
            if not query_embedding:
                return None

            # ── Modo experimental: aislamiento cognitivo por usuario ──────────
            # Cuando CME_EXPERIMENTAL_USER_ISOLATION=True, el enriquecimiento
            # consulta SOLO la instancia privada del usuario actual.
            # No consulta el área compartida, no consulta el CoreBrain,
            # no consulta otras instancias. El flujo es unidireccional.
            if settings.CME_EXPERIMENTAL_USER_ISOLATION and user_id:
                return await self._enrich_from_user_brain(
                    query, query_embedding, area_id, tenant_id, user_id, working_memory, db
                )
            # ─────────────────────────────────────────────────────────────────

            # Obtener IDs de patrones con contradicciones pendientes (para excluir)
            excluded_pattern_ids = await self._get_excluded_pattern_ids(area_id, db)

            # Detectar si el usuario está frustrado
            is_frustrated = (
                working_memory is not None and
                working_memory.detected_emotion == "frustrated"
            )

            # Consultar todas las fuentes en paralelo
            results = await asyncio.gather(
                self._query_episodes(query_embedding, area_id, db, is_frustrated),
                self._query_patterns(query_embedding, area_id, excluded_pattern_ids, db),
                self._query_methodologies(query_embedding, area_id, db),
                self._query_concept_edges(query, area_id, db),
                global_brain.query_patterns(query_embedding, tenant_id, db, MAX_GLOBAL_PATTERNS),
                global_brain.query_methodologies(query_embedding, tenant_id, db, MAX_GLOBAL_METHODOLOGIES),
                self._get_user_profile(user_id, area_id, db) if user_id else _none_coroutine(),
                self._query_universal(query_embedding, db),
                return_exceptions=True
            )

            (
                episodes,
                patterns,
                methodologies,
                concept_edges,
                global_pats,
                global_meths,
                user_profile,
                universal_pat,
            ) = results

            # Manejar excepciones de gather (fail-silent)
            episodes = episodes if not isinstance(episodes, Exception) else []
            patterns = patterns if not isinstance(patterns, Exception) else []
            methodologies = methodologies if not isinstance(methodologies, Exception) else []
            concept_edges = concept_edges if not isinstance(concept_edges, Exception) else []
            global_pats = global_pats if not isinstance(global_pats, Exception) else []
            global_meths = global_meths if not isinstance(global_meths, Exception) else []
            user_profile = user_profile if not isinstance(user_profile, Exception) else None
            universal_pat = universal_pat if not isinstance(universal_pat, Exception) else None

            # Construir payload
            sections = []

            # Principios (abstraction_level=3) — máxima prioridad
            principles = [p for p, _ in patterns if p.abstraction_level == 3][:MAX_PRINCIPLES]
            regular_patterns = [p for p, _ in patterns if p.abstraction_level < 3][:MAX_PATTERNS]

            if principles:
                lines = []
                for p in principles:
                    lines.append(f"- Principio organizacional: {p.trigger_description}")
                    if p.causal_mechanism:
                        lines.append(f"  (esto funciona porque {p.causal_mechanism})")
                sections.append("### Principios del área\n" + "\n".join(lines))

            # Episodios similares
            if episodes:
                lines = []
                for ep, score in episodes[:MAX_EPISODES]:
                    days_ago = ""
                    try:
                        from datetime import datetime, timezone
                        now = datetime.now(timezone.utc)
                        created = ep.created_at
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        days = (now - created).days
                        days_ago = f", hace {days} días" if days > 0 else ", hoy"
                    except Exception:
                        pass

                    is_failure = getattr(ep, 'is_failure_pattern', False)
                    if ep.session_arc == "resolved":
                        lines.append(f"- Resolvimos algo similar antes: {ep.strategy[:150]} (calidad: {ep.quality_score or 'N/A'}{days_ago})")
                    elif is_failure:
                        lines.append(f"- Evitar este enfoque: {ep.strategy[:150]}")
                    else:
                        lines.append(f"- Episodio relacionado: {ep.situation[:100]} → {ep.strategy[:100]}")
                if lines:
                    sections.append("### Episodios similares\n" + "\n".join(lines))

            # Patrones del área
            if regular_patterns:
                lines = []
                for p in regular_patterns:
                    if p.is_failure_pattern:
                        lines.append(f"- Evitar este enfoque: {p.trigger_description[:150]}")
                    else:
                        line = f"- Cuando {p.trigger_description[:120]}, la respuesta efectiva es {p.response_description[:120]} (confianza: {p.confidence_score:.2f})"
                        if p.causal_mechanism:
                            line += f"\n  (esto funciona porque {p.causal_mechanism[:100]})"
                        lines.append(line)
                sections.append("### Patrones del área\n" + "\n".join(lines))

            # Metodologías aprobadas
            if methodologies:
                lines = [
                    f"- {m.title}: {m.description[:150]}"
                    for m in methodologies[:MAX_METHODOLOGIES]
                ]
                sections.append("### Metodologías aprobadas\n" + "\n".join(lines))

            # Conocimiento cross-área (Global Brain)
            cross_area_lines = []
            if global_pats:
                for gp, _ in global_pats:
                    cross_area_lines.append(f"- [conocimiento cross-área]: {gp.trigger_description[:150]}")

            if global_meths:
                for gm, _ in global_meths:
                    cross_area_lines.append(f"- [metodología organizacional]: {gm.title}: {gm.description[:120]}")

            if cross_area_lines:
                sections.append("### Conocimiento cross-área\n" + "\n".join(cross_area_lines))

            # Conocimiento universal (Universal Brain) — máximo 1 patrón aprobado
            if universal_pat:
                sections.append(
                    f"### Conocimiento universal\n"
                    f"- [conocimiento universal]: {universal_pat.trigger_description[:200]}"
                )

            # Grafo de conceptos — strong links
            if concept_edges:
                links = [
                    f"{e.concept_a} ↔ {e.concept_b}"
                    for e in concept_edges[:5]
                ]
                sections.append("### Conceptos relacionados\n- " + ", ".join(links))

            # Perfil cognitivo del usuario
            if user_profile:
                level = "intermedio"
                if user_profile.expertise_level <= 0.3:
                    level = "básico"
                elif user_profile.expertise_level >= 0.7:
                    level = "avanzado"
                detail = user_profile.preferred_detail_level
                sections.append(
                    f"### Perfil del usuario\n"
                    f"- Este usuario tiene nivel {level} y prefiere respuestas {detail}."
                )

            # ── Módulos Fase 2+3 (fail-silent) ───────────────────────────────

            # AgentIdentity: inyectar si el usuario pregunta sobre el agente
            try:
                from app.config import settings as _cfg
                if _cfg.CME_ENABLE_AGENT_IDENTITY:
                    from app.cme.agent_identity import agent_identity_module
                    identity_injection = await agent_identity_module.inject_into_prompt(
                        area_id, query, db
                    )
                    if identity_injection:
                        sections.insert(0, f"### Identidad del agente\n- {identity_injection}")
            except Exception:
                pass

            # GenerativeCuriosity: inyectar directiva si el topic coincide con un gap
            try:
                from app.config import settings as _cfg
                if _cfg.CME_ENABLE_GENERATIVE_CURIOSITY:
                    from app.cme.generative_curiosity import generative_curiosity
                    session_id = (
                        working_memory.session_id
                        if working_memory and hasattr(working_memory, "session_id")
                        else "unknown"
                    )
                    curiosity_directive = await generative_curiosity.inject_if_relevant(
                        session_id, query, area_id, db
                    )
                    if curiosity_directive:
                        sections.append(f"### Curiosidad del sistema\n- {curiosity_directive}")
            except Exception:
                pass

            # CrossDomainInsights: agregar insights validados relevantes
            try:
                from app.config import settings as _cfg
                if _cfg.CME_ENABLE_CROSS_DOMAIN_INSIGHTS:
                    from app.cme.cross_domain_insight import cross_domain_insights
                    relevant_insights = await cross_domain_insights.get_relevant_insights(
                        query_embedding, area_id, db
                    )
                    if relevant_insights:
                        lines = [
                            f"- [conexión inesperada]: {ins.connection_description[:200]}"
                            for ins in relevant_insights
                        ]
                        sections.append("### Conexiones cross-dominio\n" + "\n".join(lines))
            except Exception:
                pass

            # TemporalNarrative: agregar cadenas causales relevantes
            try:
                from app.config import settings as _cfg
                if _cfg.CME_ENABLE_TEMPORAL_NARRATIVE:
                    from app.cme.temporal_narrative import temporal_narrative
                    relevant_chains = await temporal_narrative.get_relevant_chains(
                        query_embedding, area_id, db
                    )
                    if relevant_chains:
                        lines = [
                            f"- [antecedente detectado, {chain.time_delta_days:.0f} días antes]: "
                            f"{chain.causal_link_description[:200]}"
                            for chain in relevant_chains
                        ]
                        sections.append("### Cadenas temporales\n" + "\n".join(lines))
            except Exception:
                pass

            # MentalSimulation: activar si corresponde
            try:
                from app.config import settings as _cfg
                if _cfg.CME_ENABLE_MENTAL_SIMULATION:
                    from app.cme.mental_simulation import mental_simulation
                    from sqlalchemy import func as sql_func
                    # Obtener count de episodios del área
                    count_q = await db.execute(
                        select(sql_func.count(AreaEpisode.id))
                        .where(AreaEpisode.area_id == area_id)
                    )
                    area_episode_count = count_q.scalar() or 0

                    if mental_simulation.should_activate(working_memory, area_episode_count):
                        best_payload = await mental_simulation.simulate(
                            query, area_id, tenant_id, working_memory, db
                        )
                        if best_payload and best_payload != query:
                            sections.append(
                                f"### Simulación interna\n"
                                f"- El sistema evaluó múltiples enfoques. "
                                f"Contexto optimizado: {best_payload[:200]}"
                            )
            except Exception:
                pass

            if not sections:
                return None

            payload = "## Memoria cognitiva relevante\n\n" + "\n\n".join(sections)
            return payload

        except Exception as e:
            logger.warning(f"CME ContextEnricher: error en enrich (fail-silent): {e}")
            return None

    async def _enrich_from_user_brain(
        self,
        query: str,
        query_embedding: list[float],
        area_id: str,
        tenant_id: str,
        user_id: str,
        working_memory,
        db: AsyncSession,
    ) -> str | None:
        """
        Enriquecimiento en modo aislamiento cognitivo por usuario.

        Consulta SOLO:
          - UserEpisodes del usuario actual
          - UserPatterns del usuario actual
          - Cadenas temporales del usuario (TemporalNarrative sobre UserEpisodes)
          - Perfil cognitivo del usuario

        NO consulta:
          - AreaEpisodes / AreaPatterns (compartidos)
          - GlobalBrain
          - CoreBrain
          - Instancias de otros usuarios

        El flujo es unidireccional: las instancias escriben al CoreBrain,
        pero nunca leen de él.
        """
        try:
            from app.cme.user_brain import user_brain

            is_frustrated = (
                working_memory is not None and
                working_memory.detected_emotion == "frustrated"
            )

            # Consultar instancia privada del usuario en paralelo
            results = await asyncio.gather(
                user_brain.query_episodes(
                    query_embedding, user_id, area_id, db,
                    prioritize_resolved=is_frustrated
                ),
                user_brain.query_patterns(
                    query_embedding, user_id, area_id, db
                ),
                self._get_user_profile(user_id, area_id, db),
                return_exceptions=True
            )

            user_episodes, user_patterns, user_profile = results
            user_episodes = user_episodes if not isinstance(user_episodes, Exception) else []
            user_patterns = user_patterns if not isinstance(user_patterns, Exception) else []
            user_profile = user_profile if not isinstance(user_profile, Exception) else None

            sections = []

            # Episodios privados del usuario
            if user_episodes:
                lines = []
                for ep, score in user_episodes:
                    try:
                        from datetime import datetime, timezone
                        now = datetime.now(timezone.utc)
                        created = ep.created_at
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        days = (now - created).days
                        days_ago = f", hace {days} días" if days > 0 else ", hoy"
                    except Exception:
                        days_ago = ""

                    if ep.session_arc == "resolved":
                        lines.append(
                            f"- Lo que funcionó antes: {ep.strategy[:150]}"
                            f" (calidad: {ep.quality_score or 'N/A'}{days_ago})"
                        )
                        if ep.causal_explanation:
                            lines.append(f"  (porque: {ep.causal_explanation[:100]})")
                    elif ep.session_arc in ("abandoned", "degraded"):
                        lines.append(f"- Lo que no funcionó: {ep.strategy[:150]}")
                        if ep.failure_analysis:
                            lines.append(f"  (por qué falló: {ep.failure_analysis[:100]})")
                    else:
                        lines.append(f"- Episodio relacionado: {ep.situation[:100]} → {ep.strategy[:100]}")
                if lines:
                    sections.append("### Tu memoria\n" + "\n".join(lines))

            # Patrones privados del usuario
            if user_patterns:
                lines = []
                for p, sim in user_patterns:
                    if p.is_failure_pattern:
                        lines.append(f"- Evitar este enfoque: {p.trigger_description[:150]}")
                    else:
                        line = (
                            f"- Cuando {p.trigger_description[:120]}, "
                            f"lo que te funcionó: {p.response_description[:120]} "
                            f"(confianza: {p.confidence_score:.2f})"
                        )
                        if p.causal_mechanism:
                            line += f"\n  (porque: {p.causal_mechanism[:100]})"
                        lines.append(line)
                if lines:
                    sections.append("### Tus patrones\n" + "\n".join(lines))

            # Cadenas temporales sobre episodios del usuario
            try:
                if settings.CME_ENABLE_TEMPORAL_NARRATIVE:
                    from app.cme.temporal_narrative import temporal_narrative
                    # TemporalNarrative opera sobre AreaEpisodes, pero en modo aislado
                    # usamos los UserEpisodes directamente para detectar cadenas
                    user_chains = await self._query_user_temporal_chains(
                        query_embedding, user_id, area_id, db
                    )
                    if user_chains:
                        lines = [
                            f"- [antecedente tuyo, {delta:.0f} días antes]: {desc[:200]}"
                            for delta, desc in user_chains
                        ]
                        sections.append("### Tu narrativa temporal\n" + "\n".join(lines))
            except Exception:
                pass

            # Perfil cognitivo del usuario
            if user_profile:
                level = "intermedio"
                if user_profile.expertise_level <= 0.3:
                    level = "básico"
                elif user_profile.expertise_level >= 0.7:
                    level = "avanzado"
                detail = user_profile.preferred_detail_level
                sections.append(
                    f"### Tu perfil\n"
                    f"- Nivel {level}, preferencia de respuestas {detail}."
                )

            # AgentIdentity (si aplica)
            try:
                if settings.CME_ENABLE_AGENT_IDENTITY:
                    from app.cme.agent_identity import agent_identity_module
                    identity_injection = await agent_identity_module.inject_into_prompt(
                        area_id, query, db
                    )
                    if identity_injection:
                        sections.insert(0, f"### Identidad del agente\n- {identity_injection}")
            except Exception:
                pass

            # MentalSimulation (si aplica y está activo)
            try:
                if settings.CME_ENABLE_MENTAL_SIMULATION:
                    from app.cme.mental_simulation import mental_simulation
                    from sqlalchemy import func as sql_func
                    from app.models.cme import UserEpisode as _UE
                    count_q = await db.execute(
                        select(sql_func.count(_UE.id))
                        .where(_UE.user_id == user_id, _UE.area_id == area_id)
                    )
                    user_episode_count = count_q.scalar() or 0
                    if mental_simulation.should_activate(working_memory, user_episode_count):
                        best_payload = await mental_simulation.simulate(
                            query, area_id, tenant_id, working_memory, db
                        )
                        if best_payload and best_payload != query:
                            sections.append(
                                f"### Simulación interna\n"
                                f"- Contexto optimizado: {best_payload[:200]}"
                            )
            except Exception:
                pass

            if not sections:
                return None

            payload = "## Tu memoria cognitiva\n\n" + "\n\n".join(sections)
            return payload

        except Exception as e:
            logger.warning(f"CME ContextEnricher: error en _enrich_from_user_brain: {e}")
            return None

    async def _query_user_temporal_chains(
        self,
        query_embedding: list[float],
        user_id: str,
        area_id: str,
        db: AsyncSession,
    ) -> list[tuple[float, str]]:
        """
        Detecta cadenas temporales dentro de los UserEpisodes del usuario.
        Retorna lista de (time_delta_days, causal_description).
        No usa la tabla temporal_chains (que es de área) — opera directamente
        sobre los UserEpisodes del usuario.
        """
        try:
            import json as _json
            from datetime import timezone as _tz
            from app.models.cme import UserEpisode as _UE

            eps_q = await db.execute(
                select(_UE)
                .where(
                    _UE.user_id == user_id,
                    _UE.area_id == area_id,
                    _UE.situation_embedding.isnot(None),
                    _UE.extraction_status == "completed",
                )
                .order_by(_UE.created_at.desc())
                .limit(30)
            )
            episodes = eps_q.scalars().all()

            if len(episodes) < 2:
                return []

            eps_with_emb = []
            for ep in episodes:
                try:
                    emb = _json.loads(ep.situation_embedding)
                    eps_with_emb.append((ep, emb))
                except Exception:
                    continue

            chains = []
            for i in range(len(eps_with_emb)):
                ep_b, emb_b = eps_with_emb[i]
                sim_to_query = cosine_similarity(query_embedding, emb_b)
                if sim_to_query < 0.60:
                    continue

                for j in range(i + 1, len(eps_with_emb)):
                    ep_a, emb_a = eps_with_emb[j]
                    sim = cosine_similarity(emb_a, emb_b)
                    if sim < 0.65:
                        continue

                    created_a = ep_a.created_at
                    created_b = ep_b.created_at
                    if created_a.tzinfo is None:
                        created_a = created_a.replace(tzinfo=_tz.utc)
                    if created_b.tzinfo is None:
                        created_b = created_b.replace(tzinfo=_tz.utc)

                    delta = abs((created_b - created_a).total_seconds()) / 86400.0
                    if 1 <= delta <= 30:
                        desc = (
                            ep_a.causal_explanation
                            or f"Situación similar: {ep_a.situation[:120]}"
                        )
                        chains.append((delta, desc))
                        break  # un antecedente por episodio es suficiente

            return chains[:3]

        except Exception as e:
            logger.debug(f"CME ContextEnricher: error en _query_user_temporal_chains: {e}")
            return []

    async def _query_episodes(
        self,
        query_embedding: list[float],
        area_id: str,
        db: AsyncSession,
        prioritize_resolved: bool = False
    ) -> list[tuple]:
        """Busca episodios relevantes por similitud × temporal_weight."""
        try:
            eps_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.situation_embedding.isnot(None),
                    AreaEpisode.temporal_weight >= 0.1,  # excluir episodios olvidados
                    AreaEpisode.extraction_status == "completed"
                )
            )
            episodes = eps_q.scalars().all()

            scored = []
            for ep in episodes:
                try:
                    emb = json.loads(ep.situation_embedding)
                    cosine = cosine_similarity(query_embedding, emb)
                    relevance = forgetting_curve.compute_relevance_score(
                        cosine,
                        ep.temporal_weight,
                        ep.emotional_intensity or 0.0
                    )
                    if relevance >= EPISODE_MIN_RELEVANCE:
                        scored.append((ep, relevance))
                except Exception:
                    continue

            # Si el usuario está frustrado, priorizar episodios resolved
            if prioritize_resolved:
                scored.sort(key=lambda x: (x[0].session_arc == "resolved", x[1]), reverse=True)
            else:
                scored.sort(key=lambda x: x[1], reverse=True)

            return scored[:MAX_EPISODES]
        except Exception as e:
            logger.debug(f"CME ContextEnricher: error en _query_episodes: {e}")
            return []

    async def _query_patterns(
        self,
        query_embedding: list[float],
        area_id: str,
        excluded_ids: set,
        db: AsyncSession
    ) -> list[tuple]:
        """Busca patrones aprobados relevantes, excluyendo los que tienen contradicciones pendientes."""
        try:
            pats_q = await db.execute(
                select(AreaPattern)
                .where(
                    AreaPattern.area_id == area_id,
                    AreaPattern.is_approved == True,
                    AreaPattern.trigger_embedding.isnot(None)
                )
            )
            patterns = pats_q.scalars().all()

            scored = []
            for p in patterns:
                if p.id in excluded_ids:
                    continue
                try:
                    emb = json.loads(p.trigger_embedding)
                    sim = cosine_similarity(query_embedding, emb)
                    if sim >= PATTERN_MIN_SIMILARITY:
                        scored.append((p, sim))
                except Exception:
                    continue

            # Principios primero (abstraction_level desc), luego por similitud
            scored.sort(key=lambda x: (x[0].abstraction_level, x[1]), reverse=True)
            return scored[:MAX_PRINCIPLES + MAX_PATTERNS]
        except Exception as e:
            logger.debug(f"CME ContextEnricher: error en _query_patterns: {e}")
            return []

    async def _query_methodologies(
        self,
        query_embedding: list[float],
        area_id: str,
        db: AsyncSession
    ) -> list:
        """Busca metodologías aprobadas relevantes."""
        try:
            meths_q = await db.execute(
                select(AreaMethodology)
                .where(
                    AreaMethodology.area_id == area_id,
                    AreaMethodology.is_approved == True,
                    AreaMethodology.description_embedding.isnot(None)
                )
            )
            methodologies = meths_q.scalars().all()

            scored = []
            for m in methodologies:
                try:
                    emb = json.loads(m.description_embedding)
                    sim = cosine_similarity(query_embedding, emb)
                    if sim >= METHODOLOGY_MIN_SIMILARITY:
                        scored.append((m, sim))
                except Exception:
                    continue

            scored.sort(key=lambda x: x[1], reverse=True)
            return [m for m, _ in scored[:MAX_METHODOLOGIES]]
        except Exception as e:
            logger.debug(f"CME ContextEnricher: error en _query_methodologies: {e}")
            return []

    async def _query_concept_edges(
        self,
        query: str,
        area_id: str,
        db: AsyncSession
    ) -> list:
        """Busca strong concept links relacionados con el query."""
        try:
            query_lower = query.lower()
            edges_q = await db.execute(
                select(AreaConceptEdge)
                .where(
                    AreaConceptEdge.area_id == area_id,
                    AreaConceptEdge.weight >= CONCEPT_EDGE_STRONG_LINK
                )
                .order_by(AreaConceptEdge.weight.desc())
                .limit(20)
            )
            edges = edges_q.scalars().all()

            # Filtrar edges donde alguno de los conceptos aparece en el query
            relevant = [
                e for e in edges
                if e.concept_a in query_lower or e.concept_b in query_lower
            ]
            return relevant[:5]
        except Exception as e:
            logger.debug(f"CME ContextEnricher: error en _query_concept_edges: {e}")
            return []

    async def _get_excluded_pattern_ids(self, area_id: str, db: AsyncSession) -> set:
        """Obtiene IDs de patrones con contradicciones pendientes (deben excluirse)."""
        try:
            contradictions_q = await db.execute(
                select(AreaContradiction)
                .where(
                    AreaContradiction.area_id == area_id,
                    AreaContradiction.status == "pending"
                )
            )
            contradictions = contradictions_q.scalars().all()
            excluded = set()
            for c in contradictions:
                excluded.add(c.pattern_a_id)
                excluded.add(c.pattern_b_id)
            return excluded
        except Exception:
            return set()

    async def _query_universal(
        self,
        query_embedding: list[float],
        db: AsyncSession
    ):
        """
        Consulta el Universal Brain para el patrón universal más relevante. (Req 36.4)
        Retorna UniversalPattern | None.
        """
        try:
            from app.config import settings as _cfg
            if not _cfg.CME_ENABLE_UNIVERSAL_BRAIN:
                return None
            return await universal_brain.query_universal(query_embedding, db)
        except Exception as e:
            logger.debug(f"CME ContextEnricher: error en _query_universal: {e}")
            return None

    async def respond_from_memory(
        self,
        query: str,
        area_id: str,
        tenant_id: str,
        working_memory,
        db: AsyncSession,
        user_id: str | None = None
    ) -> str | None:
        """
        Genera una respuesta directamente desde la memoria cognitiva del área,
        sin necesidad del LLM. Se usa como fallback cuando el LLM no está disponible.

        Busca episodios y patrones relevantes y construye una respuesta estructurada
        desde lo que el sistema aprendió. Retorna None si no hay memoria suficiente.
        """
        try:
            query_embedding = await get_embedding(query)
            if not query_embedding:
                return None

            excluded_pattern_ids = await self._get_excluded_pattern_ids(area_id, db)
            is_frustrated = (
                working_memory is not None and
                working_memory.detected_emotion == "frustrated"
            )

            episodes, patterns, methodologies = await asyncio.gather(
                self._query_episodes(query_embedding, area_id, db, is_frustrated),
                self._query_patterns(query_embedding, area_id, excluded_pattern_ids, db),
                self._query_methodologies(query_embedding, area_id, db),
                return_exceptions=True
            )

            episodes = episodes if not isinstance(episodes, Exception) else []
            patterns = patterns if not isinstance(patterns, Exception) else []
            methodologies = methodologies if not isinstance(methodologies, Exception) else []

            if not episodes and not patterns and not methodologies:
                return None

            lines = []

            # Episodios resueltos — lo más valioso
            resolved = [(ep, s) for ep, s in episodes if ep.session_arc == "resolved"]
            if resolved:
                ep, _ = resolved[0]
                lines.append(f"Basándome en lo que aprendí de este equipo: {ep.strategy[:300]}")

            # Patrones aprobados relevantes
            approved = [(p, s) for p, s in patterns if p.abstraction_level >= 1]
            if approved and not resolved:
                p, _ = approved[0]
                lines.append(f"Lo que suele funcionar en situaciones así: {p.response_description[:300]}")
            elif approved:
                p, _ = approved[0]
                lines.append(f"Un patrón que detecté: {p.response_description[:200]}")

            # Metodologías
            if methodologies:
                m = methodologies[0]
                lines.append(f"Metodología relevante — {m.title}: {m.description[:200]}")

            if not lines:
                return None

            response = "\n\n".join(lines)
            response += "\n\n*(Respondí desde mi memoria porque el servicio de IA no está disponible ahora mismo.)*"
            return response

        except Exception as e:
            logger.warning(f"CME ContextEnricher: error en respond_from_memory: {e}")
            return None

    async def _get_user_profile(
        self,
        user_id: str,
        area_id: str,
        db: AsyncSession
    ):
        """Obtiene el perfil cognitivo del usuario para el área."""
        try:
            profile_q = await db.execute(
                select(UserCognitiveProfile)
                .where(
                    UserCognitiveProfile.user_id == user_id,
                    UserCognitiveProfile.area_id == area_id
                )
            )
            return profile_q.scalar_one_or_none()
        except Exception:
            return None


# Instancia global singleton
context_enricher = ContextEnricher()
