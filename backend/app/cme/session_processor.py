"""
Session Processor — Background task que procesa señales de sesión post-respuesta.
Se ejecuta de forma asíncrona, nunca bloquea la respuesta al usuario.
"""
import json
import logging
import re
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.cme import (
    AreaEpisode, AreaPattern, AreaMethodology, AreaKnowledgeGap,
    AreaConceptEdge, UserCognitiveProfile, RLHFDataset, AgentDrive
)
from app.models.chat import ChatSession, ChatMessage
from app.models.area import Area

logger = logging.getLogger(__name__)

# Umbrales desde configuración (con fallback a defaults)
try:
    from app.config import settings as _settings
    RLHF_QUALITY_THRESHOLD = _settings.CME_RLHF_QUALITY_THRESHOLD
    METHODOLOGY_QUALITY_THRESHOLD = _settings.CME_METHODOLOGY_QUALITY_THRESHOLD
    PATTERN_DETECTION_INTERVAL = _settings.CME_PATTERN_DETECTION_INTERVAL
except Exception:
    RLHF_QUALITY_THRESHOLD = 0.80
    METHODOLOGY_QUALITY_THRESHOLD = 0.75
    PATTERN_DETECTION_INTERVAL = 10


async def process_session_signals(
    session_id: str,
    area_id: str,
    tenant_id: str,
    user_id: str,
    db_factory
) -> None:
    """
    Background task principal. Procesa todas las señales de una sesión.
    Usa db_factory para crear una nueva sesión de DB (no reutiliza la del request).
    """
    async with db_factory() as db:
        try:
            await _run_session_processing(session_id, area_id, tenant_id, user_id, db)
        except Exception as e:
            logger.error(f"CME SessionProcessor: error procesando sesión {session_id}: {e}")


async def _run_session_processing(
    session_id: str,
    area_id: str,
    tenant_id: str,
    user_id: str,
    db: AsyncSession
) -> None:
    """Ejecuta el pipeline completo de procesamiento de sesión."""

    # 1. Extraer episodio de la sesión
    episode = None
    try:
        from app.cme.episode_extractor import episode_extractor
        episode = await episode_extractor.extract(session_id, area_id, tenant_id, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error en episode_extractor: {e}")

    if not episode:
        return  # Sesión no cumple criterios mínimos

    # 2. Calcular quality_score
    try:
        from app.cme.quality_signal_engine import quality_signal_engine
        quality_score = await quality_signal_engine.compute_score(
            session_id, episode.session_arc, db
        )
        episode.quality_score = quality_score
        await db.commit()
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error en quality_signal_engine: {e}")
        quality_score = None

    # 2b. Calcular intensidad emocional del episodio (SelectiveAttention)
    try:
        from app.config import settings as _cfg
        if _cfg.CME_ENABLE_SELECTIVE_ATTENTION:
            from app.cme.selective_attention import selective_attention
            from app.models.chat import ChatMessage
            msgs_q = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            )
            messages = msgs_q.scalars().all()
            emotional_intensity = selective_attention.compute_emotional_intensity(
                episode, messages, db
            )
            episode.emotional_intensity = emotional_intensity
            await db.commit()
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error calculando emotional_intensity: {e}")

    # 2c. Guardar episodio en UserBrain si el modo experimental está activo
    #     El UserBrain crea una copia privada del episodio scoped por user_id.
    #     Esto ocurre en paralelo con el guardado en AreaEpisode — ambos coexisten.
    try:
        from app.config import settings as _cfg
        if _cfg.CME_EXPERIMENTAL_USER_ISOLATION and user_id:
            from app.cme.user_brain import user_brain
            await user_brain.save_episode(episode, user_id, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error guardando en UserBrain: {e}")

    # 3. Actualizar concept graph
    try:
        await _update_concept_graph(episode, area_id, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error actualizando concept graph: {e}")

    # 4. Detectar knowledge gaps si sesión fue degraded/abandoned
    try:
        if episode.session_arc in ("abandoned", "degraded"):
            await _detect_knowledge_gap(episode, area_id, tenant_id, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error detectando knowledge gap: {e}")

    # 4b. Extraer failure_analysis para sesiones degraded/abandoned (AsymmetricLearning)
    try:
        from app.config import settings as _cfg
        if _cfg.CME_ENABLE_ASYMMETRIC_LEARNING and episode.session_arc in ("abandoned", "degraded"):
            from app.cme.asymmetric_learning import asymmetric_learning
            await asymmetric_learning.extract_failure_analysis(session_id, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error en asymmetric_learning: {e}")

    # 5. Actualizar UserCognitiveProfile
    try:
        await _update_user_profile(user_id, area_id, tenant_id, session_id, episode, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error actualizando user profile: {e}")

    # 6. Evaluar methodology promotion
    try:
        if quality_score and quality_score >= METHODOLOGY_QUALITY_THRESHOLD and episode.session_arc == "resolved":
            await _promote_methodology(episode, area_id, tenant_id, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error en methodology promotion: {e}")

    # 7. Incrementar contador y disparar PatternDetector si es múltiplo de 10
    try:
        await _maybe_run_pattern_detection(area_id, tenant_id, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error en pattern detection trigger: {e}")

    # 8. Actualizar AgentDrives (usando módulo real)
    try:
        from app.config import settings as _cfg
        if _cfg.CME_ENABLE_AGENT_DRIVES:
            from app.cme.agent_drives import agent_drives
            await agent_drives.update_after_session(
                area_id=area_id,
                session_arc=episode.session_arc,
                quality_score=episode.quality_score,
                db=db,
            )
            await agent_drives.check_tension_alerts(area_id, tenant_id, db)
        else:
            # Fallback al método interno si el módulo está desactivado
            await _update_agent_drives(area_id, tenant_id, episode, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error actualizando agent drives: {e}")

    # 9. Guardar en RLHF dataset si califica
    try:
        if quality_score and quality_score >= RLHF_QUALITY_THRESHOLD and episode.session_arc == "resolved":
            await _save_rlhf_record(session_id, area_id, tenant_id, quality_score, episode.session_arc, db)
    except Exception as e:
        logger.warning(f"CME SessionProcessor: error guardando RLHF record: {e}")


async def _update_concept_graph(episode: AreaEpisode, area_id: str, db: AsyncSession) -> None:
    """
    Extrae conceptos de situation y strategy, actualiza area_concept_edges.
    Normaliza a lowercase, sin puntuación. Incrementa weight en 1.0 por par co-ocurrente.
    """
    text = f"{episode.situation} {episode.strategy}"
    concepts = _extract_concepts(text)

    if len(concepts) < 2:
        return

    now = datetime.now(timezone.utc)

    for i in range(len(concepts)):
        for j in range(i + 1, len(concepts)):
            concept_a = concepts[i]
            concept_b = concepts[j]

            # Buscar edge existente (en ambas direcciones)
            edge_q = await db.execute(
                select(AreaConceptEdge)
                .where(
                    AreaConceptEdge.area_id == area_id,
                    (
                        (AreaConceptEdge.concept_a == concept_a) &
                        (AreaConceptEdge.concept_b == concept_b)
                    ) | (
                        (AreaConceptEdge.concept_a == concept_b) &
                        (AreaConceptEdge.concept_b == concept_a)
                    )
                )
            )
            edge = edge_q.scalar_one_or_none()

            if edge:
                edge.weight += 1.0
                edge.last_reinforced_at = now
            else:
                edge = AreaConceptEdge(
                    area_id=area_id,
                    concept_a=concept_a,
                    concept_b=concept_b,
                    weight=1.0,
                    last_reinforced_at=now,
                )
                db.add(edge)

    await db.commit()


def _extract_concepts(text: str) -> list[str]:
    """
    Extrae conceptos (palabras clave de 2-5 palabras) del texto.
    Normaliza a lowercase, sin puntuación.
    """
    # Normalizar
    text_lower = re.sub(r'[^\w\s]', '', text.lower())
    words = [w for w in text_lower.split() if len(w) > 3]

    # Extraer bigramas y trigramas como conceptos
    concepts = set()

    # Palabras individuales significativas (> 5 chars)
    for word in words:
        if len(word) > 5:
            concepts.add(word)

    # Bigramas
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        if len(bigram) > 8:
            concepts.add(bigram)

    return list(concepts)[:20]  # máximo 20 conceptos por episodio


async def _detect_knowledge_gap(
    episode: AreaEpisode,
    area_id: str,
    tenant_id: str,
    db: AsyncSession
) -> None:
    """
    Detecta knowledge gap si no hay episodio/patrón/metodología similar (cosine >= 0.65).
    Hace upsert en area_knowledge_gaps.
    """
    if not episode.situation_embedding:
        return

    from app.rag import cosine_similarity, get_embedding
    from app.models.cme import AreaPattern, AreaMethodology

    query_emb = json.loads(episode.situation_embedding)
    now = datetime.now(timezone.utc)

    # Verificar si hay episodios similares (excluyendo el actual)
    eps_q = await db.execute(
        select(AreaEpisode)
        .where(
            AreaEpisode.area_id == area_id,
            AreaEpisode.id != episode.id,
            AreaEpisode.situation_embedding.isnot(None),
            AreaEpisode.extraction_status == "completed"
        )
    )
    episodes = eps_q.scalars().all()

    for ep in episodes:
        try:
            emb = json.loads(ep.situation_embedding)
            if cosine_similarity(query_emb, emb) >= 0.65:
                return  # Hay conocimiento similar, no es un gap
        except Exception:
            continue

    # Verificar patrones similares
    pats_q = await db.execute(
        select(AreaPattern)
        .where(
            AreaPattern.area_id == area_id,
            AreaPattern.trigger_embedding.isnot(None)
        )
    )
    for pat in pats_q.scalars().all():
        try:
            emb = json.loads(pat.trigger_embedding)
            if cosine_similarity(query_emb, emb) >= 0.65:
                return
        except Exception:
            continue

    # Es un gap — upsert
    topic = episode.situation[:200]
    topic_emb = episode.situation_embedding

    # Buscar gap existente similar
    gaps_q = await db.execute(
        select(AreaKnowledgeGap)
        .where(
            AreaKnowledgeGap.area_id == area_id,
            AreaKnowledgeGap.status == "pending"
        )
    )
    existing_gap = None
    for gap in gaps_q.scalars().all():
        if gap.topic_embedding:
            try:
                gap_emb = json.loads(gap.topic_embedding)
                if cosine_similarity(query_emb, gap_emb) >= 0.70:
                    existing_gap = gap
                    break
            except Exception:
                continue

    if existing_gap:
        existing_gap.occurrence_count += 1
        existing_gap.last_seen_at = now
    else:
        new_gap = AreaKnowledgeGap(
            area_id=area_id,
            tenant_id=tenant_id,
            topic_description=topic,
            topic_embedding=topic_emb,
            occurrence_count=1,
            status="pending",
        )
        db.add(new_gap)

    await db.commit()


async def _update_user_profile(
    user_id: str,
    area_id: str,
    tenant_id: str,
    session_id: str,
    episode: AreaEpisode,
    db: AsyncSession
) -> None:
    """Actualiza el UserCognitiveProfile del usuario para el área."""
    # Obtener o crear perfil
    profile_q = await db.execute(
        select(UserCognitiveProfile)
        .where(
            UserCognitiveProfile.user_id == user_id,
            UserCognitiveProfile.area_id == area_id
        )
    )
    profile = profile_q.scalar_one_or_none()

    if not profile:
        profile = UserCognitiveProfile(
            user_id=user_id,
            area_id=area_id,
            tenant_id=tenant_id,
        )
        db.add(profile)

    # Obtener mensajes del usuario en la sesión
    msgs_q = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "user"
        )
    )
    user_msgs = msgs_q.scalars().all()

    if not user_msgs:
        await db.commit()
        return

    # Actualizar avg_message_length
    avg_len = sum(len(m.content) for m in user_msgs) / len(user_msgs)
    if profile.avg_message_length == 0:
        profile.avg_message_length = avg_len
    else:
        profile.avg_message_length = (profile.avg_message_length + avg_len) / 2

    # Actualizar expertise_level basado en complejidad léxica (longitud promedio de palabras)
    all_words = " ".join(m.content for m in user_msgs).split()
    if all_words:
        avg_word_len = sum(len(w) for w in all_words) / len(all_words)
        # Normalizar: palabras de 4-5 chars = básico, 6-7 = intermedio, 8+ = avanzado
        expertise = min(1.0, max(0.0, (avg_word_len - 4) / 6))
        profile.expertise_level = round((profile.expertise_level + expertise) / 2, 4)

    # Actualizar reformulation_rate
    from app.analytics import REPHRASED_SIGNALS
    all_text = " ".join(m.content.lower() for m in user_msgs)
    has_rephrasing = any(s in all_text for s in REPHRASED_SIGNALS)
    if has_rephrasing:
        profile.reformulation_rate = min(1.0, profile.reformulation_rate + 0.1)
    else:
        profile.reformulation_rate = max(0.0, profile.reformulation_rate - 0.02)

    # Actualizar frustration_frequency
    if episode.session_arc in ("abandoned", "degraded"):
        profile.frustration_frequency = min(1.0, profile.frustration_frequency + 0.1)
    elif episode.session_arc == "resolved":
        profile.frustration_frequency = max(0.0, profile.frustration_frequency - 0.05)

    await db.commit()


async def _promote_methodology(
    episode: AreaEpisode,
    area_id: str,
    tenant_id: str,
    db: AsyncSession
) -> None:
    """
    Crea una AreaMethodology si no existe una con source_episode_ids solapados.
    Req 6.1
    """
    # Verificar si ya existe metodología con este episodio como fuente
    meths_q = await db.execute(
        select(AreaMethodology)
        .where(AreaMethodology.area_id == area_id)
    )
    for meth in meths_q.scalars().all():
        try:
            source_ids = json.loads(meth.source_episode_ids or "[]")
            if episode.id in source_ids:
                return  # Ya existe
        except Exception:
            continue

    # Crear nueva metodología pendiente de aprobación
    methodology = AreaMethodology(
        area_id=area_id,
        tenant_id=tenant_id,
        title=f"Metodología: {episode.situation[:60]}",
        description=f"Estrategia validada: {episode.strategy[:300]}. Resultado: {episode.outcome[:200]}",
        source_episode_ids=json.dumps([episode.id]),
        is_approved=False,
    )
    db.add(methodology)
    await db.commit()
    logger.info(f"CME SessionProcessor: metodología promovida para episodio {episode.id}")


async def _maybe_run_pattern_detection(
    area_id: str,
    tenant_id: str,
    db: AsyncSession
) -> None:
    """
    Incrementa episode_count_since_last_detection.
    Si alcanza múltiplo de 10, dispara PatternDetector.
    """
    area_q = await db.execute(select(Area).where(Area.id == area_id))
    area = area_q.scalar_one_or_none()
    if not area:
        return

    area.episode_count_since_last_detection += 1

    if area.episode_count_since_last_detection % PATTERN_DETECTION_INTERVAL == 0:
        await db.commit()
        try:
            from app.cme.pattern_detector import pattern_detector
            await pattern_detector.run_for_area(area_id, tenant_id, db)
        except Exception as e:
            logger.warning(f"CME SessionProcessor: error en pattern_detector: {e}")
    else:
        await db.commit()


async def _update_agent_drives(
    area_id: str,
    tenant_id: str,
    episode: AreaEpisode,
    db: AsyncSession
) -> None:
    """
    Actualiza la tensión de los drives del agente después de la sesión.
    Req 27.2
    """
    try:
        from app.models.cme import AgentDrive
        drives_q = await db.execute(
            select(AgentDrive)
            .where(
                AgentDrive.area_id == area_id,
                AgentDrive.is_enabled == True
            )
        )
        drives = drives_q.scalars().all()

        for drive in drives:
            if drive.drive_type == "quality_maximization":
                if episode.quality_score and episode.quality_score < 0.6:
                    drive.tension = min(1.0, drive.tension + 0.15)
                elif episode.quality_score and episode.quality_score >= 0.8:
                    drive.tension = max(0.0, drive.tension - 0.05)

            elif drive.drive_type == "gap_reduction":
                if episode.session_arc in ("abandoned", "degraded"):
                    drive.tension = min(1.0, drive.tension + 0.1)
                elif episode.session_arc == "resolved":
                    drive.tension = max(0.0, drive.tension - 0.03)

        await db.commit()

        # Evaluar si algún drive supera el umbral de alerta
        from app.cme.proactive_intelligence import proactive_intelligence
        for drive in drives:
            if drive.tension > 0.7:
                await proactive_intelligence.evaluate_drives(area_id, db)
                break

    except Exception as e:
        logger.debug(f"CME SessionProcessor: error en _update_agent_drives: {e}")


async def _save_rlhf_record(
    session_id: str,
    area_id: str,
    tenant_id: str,
    quality_score: float,
    session_arc: str,
    db: AsyncSession
) -> None:
    """
    Guarda un registro RLHF anonimizado si quality_score >= 0.80 y arc = resolved.
    Req 17.1, 17.2, 17.3
    """
    # Obtener mensajes de la sesión
    msgs_q = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    messages = msgs_q.scalars().all()

    if not messages:
        return

    # Anonimizar: reemplazar emails, teléfonos y nombres propios
    def anonymize(text: str) -> str:
        text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', text)
        text = re.sub(r'\b\d{7,15}\b', '[TELEFONO]', text)
        return text

    message_pairs = [
        {"role": m.role, "content": anonymize(m.content)}
        for m in messages
    ]

    rlhf = RLHFDataset(
        tenant_id=tenant_id,
        area_id=area_id,
        session_id=session_id,
        quality_score=quality_score,
        session_arc=session_arc,
        message_pairs=json.dumps(message_pairs, ensure_ascii=False),
    )
    db.add(rlhf)
    await db.commit()
    logger.info(f"CME SessionProcessor: RLHF record guardado para sesión {session_id} (score={quality_score:.2f})")
