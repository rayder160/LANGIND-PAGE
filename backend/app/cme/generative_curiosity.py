"""
Generative Curiosity — El agente genera preguntas sobre sus propias brechas de conocimiento.

Durante la consolidación nocturna, genera preguntas de investigación para los top 5 gaps.
Durante el chat, inyecta directivas cuando el tema del usuario coincide con un gap pendiente.

Verificar settings.CME_ENABLE_GENERATIVE_CURIOSITY antes de ejecutar.
"""
import json
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaKnowledgeGap, CuriosityQueue
from app.rag import cosine_similarity, get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

CURIOSITY_RELEVANCE_THRESHOLD = 0.65
MAX_GAPS_TO_PROCESS = 5


class GenerativeCuriosity:

    async def generate_questions_for_gaps(
        self,
        area_id: str,
        db: AsyncSession
    ) -> list[CuriosityQueue]:
        """
        Genera preguntas de curiosidad para los top 5 gaps por occurrence_count.
        Solo genera si no existe ya una pregunta pendiente para ese gap.
        Retorna lista de CuriosityQueue creados.
        """
        if not settings.CME_ENABLE_GENERATIVE_CURIOSITY:
            return []

        created = []
        try:
            # Top 5 gaps pendientes por occurrence_count
            gaps_q = await db.execute(
                select(AreaKnowledgeGap)
                .where(
                    AreaKnowledgeGap.area_id == area_id,
                    AreaKnowledgeGap.status == "pending",
                )
                .order_by(AreaKnowledgeGap.occurrence_count.desc())
                .limit(MAX_GAPS_TO_PROCESS)
            )
            gaps = gaps_q.scalars().all()

            for gap in gaps:
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
                    question_text = await self._generate_question(gap)
                    if not question_text:
                        continue

                    curiosity = CuriosityQueue(
                        area_id=area_id,
                        gap_id=gap.id,
                        question_text=question_text,
                        status="pending",
                    )
                    db.add(curiosity)
                    created.append(curiosity)

                except Exception as e:
                    logger.debug(
                        f"CME GenerativeCuriosity: error procesando gap {gap.id}: {e}"
                    )

            if created:
                await db.commit()
                logger.info(
                    f"CME GenerativeCuriosity: {len(created)} preguntas generadas "
                    f"para área {area_id}"
                )

        except Exception as e:
            logger.warning(f"CME GenerativeCuriosity: error en generate_questions_for_gaps: {e}")

        return created

    async def inject_if_relevant(
        self,
        session_id: str,
        topic: str,
        area_id: str,
        db: AsyncSession
    ) -> str | None:
        """
        Si el topic del usuario coincide con un gap pendiente (cosine >= 0.65),
        retorna una directiva para incluir en el system prompt.
        Retorna None si no hay coincidencia o si el módulo está desactivado.
        """
        if not settings.CME_ENABLE_GENERATIVE_CURIOSITY:
            return None

        try:
            topic_emb = await get_embedding(topic)
            if not topic_emb:
                return None

            # Obtener gaps pendientes con embedding
            gaps_q = await db.execute(
                select(AreaKnowledgeGap)
                .where(
                    AreaKnowledgeGap.area_id == area_id,
                    AreaKnowledgeGap.status == "pending",
                    AreaKnowledgeGap.topic_embedding.isnot(None),
                )
                .order_by(AreaKnowledgeGap.occurrence_count.desc())
                .limit(20)
            )
            gaps = gaps_q.scalars().all()

            best_gap = None
            best_sim = 0.0

            for gap in gaps:
                try:
                    gap_emb = json.loads(gap.topic_embedding)
                    sim = cosine_similarity(topic_emb, gap_emb)
                    if sim >= CURIOSITY_RELEVANCE_THRESHOLD and sim > best_sim:
                        best_sim = sim
                        best_gap = gap
                except Exception:
                    continue

            if not best_gap:
                return None

            # Buscar si hay pregunta pendiente para este gap
            question_q = await db.execute(
                select(CuriosityQueue)
                .where(
                    CuriosityQueue.gap_id == best_gap.id,
                    CuriosityQueue.status == "pending",
                )
            )
            question = question_q.scalar_one_or_none()

            if question:
                directive = (
                    f"[Curiosidad del sistema] El área tiene una brecha de conocimiento "
                    f"relacionada con este tema. Si es posible, intenta responder: "
                    f"{question.question_text}"
                )
            else:
                directive = (
                    f"[Curiosidad del sistema] Este tema ha generado dudas recurrentes "
                    f"({best_gap.occurrence_count} veces). Proporciona una respuesta "
                    f"especialmente clara y completa."
                )

            return directive[:400]

        except Exception as e:
            logger.warning(f"CME GenerativeCuriosity: error en inject_if_relevant: {e}")
            return None

    async def _generate_question(self, gap: AreaKnowledgeGap) -> str | None:
        """Genera una pregunta de curiosidad para un knowledge gap via LLM."""
        fallback = (
            f"¿Cómo podemos abordar mejor el tema: '{gap.topic_description[:150]}'?"
        )

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

        except Exception as e:
            logger.debug(f"CME GenerativeCuriosity: LLM falló para gap {gap.id}: {e}")

        return fallback


# Instancia global singleton
generative_curiosity = GenerativeCuriosity()
