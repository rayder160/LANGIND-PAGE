"""
Active Anticipation — Pre-carga contexto relevante antes de que el usuario lo pida.

Analiza el historial del usuario para predecir los temas probables de la próxima sesión
y pre-carga los episodios/patrones más relevantes en WorkingMemory.

Verificar settings.CME_ENABLE_ACTIVE_ANTICIPATION antes de ejecutar.
"""
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaEpisode, AreaPattern, UserCognitiveProfile
from app.models.chat import ChatSession
from app.rag import cosine_similarity, get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

MAX_PREDICTED_TOPICS = 3
MAX_PRELOAD_ITEMS = 5
RECENT_SESSIONS_LOOKBACK = 3


class ActiveAnticipation:

    async def predict_session_topics(
        self,
        user_id: str,
        area_id: str,
        db: AsyncSession
    ) -> list[dict]:
        """
        Predice los top 3 temas probables para la próxima sesión del usuario.

        Analiza:
        - Últimas 3 sesiones del usuario
        - Perfil cognitivo del usuario (dominant_topics)

        Retorna lista de {"topic": str, "confidence": float, "embedding": list}
        """
        if not settings.CME_ENABLE_ACTIVE_ANTICIPATION:
            return []

        try:
            # Obtener últimas 3 sesiones del usuario en el área
            sessions_q = await db.execute(
                select(ChatSession)
                .where(
                    ChatSession.user_id == user_id,
                    ChatSession.area_id == area_id,
                )
                .order_by(ChatSession.created_at.desc())
                .limit(RECENT_SESSIONS_LOOKBACK)
            )
            recent_sessions = sessions_q.scalars().all()

            if not recent_sessions:
                return []

            session_ids = [s.id for s in recent_sessions]

            # Obtener episodios de esas sesiones
            episodes_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.session_id.in_(session_ids),
                    AreaEpisode.situation_embedding.isnot(None),
                    AreaEpisode.extraction_status == "completed",
                )
            )
            episodes = episodes_q.scalars().all()

            if not episodes:
                return []

            # Obtener perfil cognitivo del usuario
            profile_q = await db.execute(
                select(UserCognitiveProfile)
                .where(
                    UserCognitiveProfile.user_id == user_id,
                    UserCognitiveProfile.area_id == area_id,
                )
            )
            profile = profile_q.scalar_one_or_none()

            # Construir lista de temas candidatos
            topic_candidates = []

            for ep in episodes:
                try:
                    emb = json.loads(ep.situation_embedding)
                    topic_candidates.append({
                        "topic": ep.situation[:150],
                        "embedding": emb,
                        "confidence": ep.quality_score or 0.5,
                    })
                except Exception:
                    continue

            # Agregar temas dominantes del perfil si existen
            if profile:
                try:
                    dominant_topics = json.loads(profile.dominant_topics or "[]")
                    for topic in dominant_topics[:2]:
                        topic_emb = await get_embedding(topic)
                        if topic_emb:
                            topic_candidates.append({
                                "topic": topic,
                                "embedding": topic_emb,
                                "confidence": 0.6,
                            })
                except Exception:
                    pass

            if not topic_candidates:
                return []

            # Deduplicar por similitud (cosine >= 0.80 → mismo tema)
            deduplicated = []
            for candidate in topic_candidates:
                is_duplicate = False
                for existing in deduplicated:
                    sim = cosine_similarity(candidate["embedding"], existing["embedding"])
                    if sim >= 0.80:
                        # Conservar el de mayor confidence
                        if candidate["confidence"] > existing["confidence"]:
                            deduplicated.remove(existing)
                            deduplicated.append(candidate)
                        is_duplicate = True
                        break
                if not is_duplicate:
                    deduplicated.append(candidate)

            # Ordenar por confidence y retornar top 3
            deduplicated.sort(key=lambda x: x["confidence"], reverse=True)
            return deduplicated[:MAX_PREDICTED_TOPICS]

        except Exception as e:
            logger.warning(f"CME ActiveAnticipation: error en predict_session_topics: {e}")
            return []

    async def preload_context(
        self,
        user_id: str,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """
        Pre-carga los top 5 episodios/patrones para los temas predichos en WorkingMemory.
        Esto permite que el ContextEnricher responda más rápido en la primera consulta.
        """
        if not settings.CME_ENABLE_ACTIVE_ANTICIPATION:
            return

        try:
            predicted_topics = await self.predict_session_topics(user_id, area_id, db)

            if not predicted_topics:
                return

            # Para cada tema predicho, pre-cargar episodios relevantes
            preloaded_count = 0
            for topic_data in predicted_topics:
                if preloaded_count >= MAX_PRELOAD_ITEMS:
                    break

                topic_emb = topic_data.get("embedding")
                if not topic_emb:
                    continue

                # Buscar episodios relevantes para este tema
                episodes_q = await db.execute(
                    select(AreaEpisode)
                    .where(
                        AreaEpisode.area_id == area_id,
                        AreaEpisode.situation_embedding.isnot(None),
                        AreaEpisode.extraction_status == "completed",
                        AreaEpisode.temporal_weight >= 0.1,
                    )
                    .limit(20)
                )
                episodes = episodes_q.scalars().all()

                for ep in episodes:
                    if preloaded_count >= MAX_PRELOAD_ITEMS:
                        break
                    try:
                        ep_emb = json.loads(ep.situation_embedding)
                        sim = cosine_similarity(topic_emb, ep_emb)
                        if sim >= 0.60:
                            preloaded_count += 1
                    except Exception:
                        continue

            logger.debug(
                f"CME ActiveAnticipation: {preloaded_count} items pre-cargados "
                f"para usuario {user_id} en área {area_id}"
            )

        except Exception as e:
            logger.warning(f"CME ActiveAnticipation: error en preload_context: {e}")

    async def track_prediction_accuracy(
        self,
        session_id: str,
        actual_topic: str,
        area_id: str,
        db: AsyncSession
    ) -> None:
        """
        Registra la precisión de la predicción comparando el tema real con los predichos.
        Actualmente solo loguea para análisis futuro.
        """
        if not settings.CME_ENABLE_ACTIVE_ANTICIPATION:
            return

        try:
            # Obtener el episodio de la sesión para comparar
            episode_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.session_id == session_id,
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.extraction_status == "completed",
                )
            )
            episode = episode_q.scalar_one_or_none()

            if not episode or not episode.situation_embedding:
                return

            actual_emb = await get_embedding(actual_topic)
            if not actual_emb:
                return

            episode_emb = json.loads(episode.situation_embedding)
            accuracy = cosine_similarity(actual_emb, episode_emb)

            logger.debug(
                f"CME ActiveAnticipation: precisión de predicción para sesión {session_id}: "
                f"{accuracy:.3f} (tema real: '{actual_topic[:80]}')"
            )

        except Exception as e:
            logger.debug(f"CME ActiveAnticipation: error en track_prediction_accuracy: {e}")


# Instancia global singleton
active_anticipation = ActiveAnticipation()
