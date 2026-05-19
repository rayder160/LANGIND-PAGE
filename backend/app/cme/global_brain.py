"""
Global Brain — Agrega patrones y metodologías anonimizados cross-área a nivel tenant.
NUNCA almacena contenido raw de conversaciones — solo descripciones de patrones.
"""
import json
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import GlobalPattern, GlobalMethodology, AreaPattern, AreaMethodology, UniversalPattern
from app.rag import cosine_similarity, get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

GLOBAL_SIMILARITY_THRESHOLD = 0.75
GLOBAL_METHODOLOGY_THRESHOLD = 0.75
AUTO_PROMOTE_CONFIDENCE = 0.8
AUTO_PROMOTE_DISTINCT_USERS = 3


class GlobalBrain:

    async def promote_pattern(
        self,
        area_pattern: AreaPattern,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """
        Promueve un patrón de área al Global Brain.
        NUNCA almacena situation/strategy/outcome raw.
        Solo almacena trigger_description y response_description.
        Verifica tenant_id para aislamiento estricto.
        """
        try:
            if not area_pattern.trigger_embedding:
                return

            trigger_emb = json.loads(area_pattern.trigger_embedding)

            # Buscar patrón global similar (cosine ≥ 0.75) del mismo tenant
            global_patterns_q = await db.execute(
                select(GlobalPattern)
                .where(
                    GlobalPattern.tenant_id == tenant_id,
                    GlobalPattern.trigger_embedding.isnot(None)
                )
            )
            global_patterns = global_patterns_q.scalars().all()

            existing_global = None
            for gp in global_patterns:
                try:
                    gp_emb = json.loads(gp.trigger_embedding)
                    sim = cosine_similarity(trigger_emb, gp_emb)
                    if sim >= GLOBAL_SIMILARITY_THRESHOLD:
                        existing_global = gp
                        break
                except Exception:
                    continue

            if existing_global:
                # Actualizar patrón global existente
                source_ids = json.loads(existing_global.source_area_ids or "[]")
                if area_pattern.area_id not in source_ids:
                    source_ids.append(area_pattern.area_id)
                existing_global.source_area_ids = json.dumps(source_ids)
                existing_global.episode_count += area_pattern.episode_count
                # Actualizar confidence como promedio ponderado
                existing_global.confidence_score = round(
                    (existing_global.confidence_score + area_pattern.confidence_score) / 2, 4
                )
                existing_global.diversity_score = round(
                    max(existing_global.diversity_score, area_pattern.diversity_score), 4
                )
                await db.commit()
                logger.info(f"CME GlobalBrain: patrón global {existing_global.id} actualizado")
            else:
                # Crear nuevo patrón global — SOLO descripciones, nunca raw content
                new_global = GlobalPattern(
                    tenant_id=tenant_id,
                    trigger_description=area_pattern.trigger_description,  # descripción anonimizada
                    trigger_embedding=area_pattern.trigger_embedding,
                    response_description=area_pattern.response_description,  # descripción anonimizada
                    confidence_score=area_pattern.confidence_score,
                    diversity_score=area_pattern.diversity_score,
                    source_area_ids=json.dumps([area_pattern.area_id]),
                    episode_count=area_pattern.episode_count,
                    temporal_relevance_index=1.0,
                )
                db.add(new_global)
                await db.commit()
                logger.info(f"CME GlobalBrain: nuevo patrón global creado para tenant {tenant_id}")

        except Exception as e:
            logger.error(f"CME GlobalBrain: error en promote_pattern: {e}")

    async def promote_methodology(
        self,
        area_methodology: AreaMethodology,
        tenant_id: str,
        db: AsyncSession
    ) -> None:
        """
        Promueve una metodología de área al Global Brain.
        Solo almacena title y description — nunca source_episode_ids con contenido raw.
        """
        try:
            if not area_methodology.description_embedding:
                # Generar embedding si no existe
                emb = await get_embedding(area_methodology.description)
                if not emb:
                    return
                area_methodology.description_embedding = json.dumps(emb)
                await db.commit()

            desc_emb = json.loads(area_methodology.description_embedding)

            # Buscar metodología global similar del mismo tenant
            global_meths_q = await db.execute(
                select(GlobalMethodology)
                .where(
                    GlobalMethodology.tenant_id == tenant_id,
                    GlobalMethodology.description_embedding.isnot(None)
                )
            )
            global_meths = global_meths_q.scalars().all()

            existing_global = None
            for gm in global_meths:
                try:
                    gm_emb = json.loads(gm.description_embedding)
                    sim = cosine_similarity(desc_emb, gm_emb)
                    if sim >= GLOBAL_METHODOLOGY_THRESHOLD:
                        existing_global = gm
                        break
                except Exception:
                    continue

            if existing_global:
                # Actualizar source_area_ids
                source_ids = json.loads(existing_global.source_area_ids or "[]")
                if area_methodology.area_id not in source_ids:
                    source_ids.append(area_methodology.area_id)
                    existing_global.source_area_ids = json.dumps(source_ids)
                    await db.commit()
                logger.info(f"CME GlobalBrain: metodología global {existing_global.id} actualizada")
            else:
                # Crear nueva metodología global
                new_global = GlobalMethodology(
                    tenant_id=tenant_id,
                    title=area_methodology.title,
                    description=area_methodology.description,
                    description_embedding=area_methodology.description_embedding,
                    source_area_ids=json.dumps([area_methodology.area_id]),
                )
                db.add(new_global)
                await db.commit()
                logger.info(f"CME GlobalBrain: nueva metodología global creada para tenant {tenant_id}")

        except Exception as e:
            logger.error(f"CME GlobalBrain: error en promote_methodology: {e}")

    async def decay_temporal_relevance(self, tenant_id: str, db: AsyncSession) -> None:
        """
        Reduce temporal_relevance_index de patrones globales cuando las áreas fuente
        reportan episodios contradictorios (Req 12.6).
        """
        try:
            # Obtener patrones globales del tenant
            gp_q = await db.execute(
                select(GlobalPattern)
                .where(GlobalPattern.tenant_id == tenant_id)
            )
            global_patterns = gp_q.scalars().all()

            for gp in global_patterns:
                source_ids = json.loads(gp.source_area_ids or "[]")
                if not source_ids:
                    continue

                # Verificar si alguna área fuente tiene contradicciones recientes
                from app.models.cme import AreaContradiction
                for area_id in source_ids:
                    contradictions_q = await db.execute(
                        select(AreaContradiction)
                        .where(
                            AreaContradiction.area_id == area_id,
                            AreaContradiction.status == "pending"
                        )
                    )
                    if contradictions_q.scalars().first():
                        # Reducir relevancia temporal
                        gp.temporal_relevance_index = round(
                            max(0.0, gp.temporal_relevance_index - 0.1), 4
                        )
                        break

            await db.commit()
        except Exception as e:
            logger.error(f"CME GlobalBrain: error en decay_temporal_relevance: {e}")

    async def query_patterns(
        self,
        query_embedding: list[float],
        tenant_id: str,
        db: AsyncSession,
        top_k: int = 2,
        min_similarity: float = 0.65
    ) -> list[tuple]:
        """
        Busca patrones globales relevantes para un query.
        Retorna lista de (GlobalPattern, similarity_score).
        Filtra estrictamente por tenant_id.
        """
        try:
            gp_q = await db.execute(
                select(GlobalPattern)
                .where(
                    GlobalPattern.tenant_id == tenant_id,
                    GlobalPattern.trigger_embedding.isnot(None),
                    GlobalPattern.temporal_relevance_index >= 0.3
                )
            )
            global_patterns = gp_q.scalars().all()

            scored = []
            for gp in global_patterns:
                try:
                    gp_emb = json.loads(gp.trigger_embedding)
                    sim = cosine_similarity(query_embedding, gp_emb)
                    if sim >= min_similarity:
                        scored.append((gp, sim))
                except Exception:
                    continue

            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:top_k]
        except Exception as e:
            logger.error(f"CME GlobalBrain: error en query_patterns: {e}")
            return []

    async def query_methodologies(
        self,
        query_embedding: list[float],
        tenant_id: str,
        db: AsyncSession,
        top_k: int = 1,
        min_similarity: float = 0.65
    ) -> list[tuple]:
        """
        Busca metodologías globales relevantes para un query.
        Filtra estrictamente por tenant_id.
        """
        try:
            gm_q = await db.execute(
                select(GlobalMethodology)
                .where(
                    GlobalMethodology.tenant_id == tenant_id,
                    GlobalMethodology.description_embedding.isnot(None)
                )
            )
            global_meths = gm_q.scalars().all()

            scored = []
            for gm in global_meths:
                try:
                    gm_emb = json.loads(gm.description_embedding)
                    sim = cosine_similarity(query_embedding, gm_emb)
                    if sim >= min_similarity:
                        scored.append((gm, sim))
                except Exception:
                    continue

            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:top_k]
        except Exception as e:
            logger.error(f"CME GlobalBrain: error en query_methodologies: {e}")
            return []


# Instancia global singleton
global_brain = GlobalBrain()


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSAL BRAIN — Conocimiento cross-tenant (Req 36)
# ─────────────────────────────────────────────────────────────────────────────

UNIVERSAL_PROMOTE_CONFIDENCE = 0.85
UNIVERSAL_PROMOTE_MIN_AREAS = 3
UNIVERSAL_PROMOTE_MIN_CYCLES = 2
UNIVERSAL_QUERY_MIN_SIMILARITY = 0.60


class UniversalBrain:
    """
    Universal Brain — Agrega conocimiento abstracto cross-tenant.

    NUNCA almacena: nombres de tenant/área, identificadores de usuario,
    etiquetas de industria ni terminología específica de empresa. (Req 36.3)

    El conocimiento se abstrae en 3 niveles eliminando toda referencia de dominio
    antes de ser almacenado.
    """

    async def evaluate_for_promotion(
        self,
        global_pattern: GlobalPattern,
        db: AsyncSession
    ) -> bool:
        """
        Evalúa si un GlobalPattern cumple los criterios para ser promovido al Universal Brain.

        Criterios (Req 36.2):
        - confidence_score ≥ 0.85
        - source_area_ids abarca ≥ 3 áreas distintas
        - ha pasado por 2+ ciclos de consolidación (episode_count como proxy)

        Si cumple: llama al LLM para aplicar abstracción de 3 niveles eliminando
        referencias específicas de dominio, y crea registro en universal_patterns
        con status=pending_approval.

        Retorna True si se creó el registro de promoción, False en caso contrario.
        """
        try:
            # Verificar confidence_score ≥ 0.85
            if global_pattern.confidence_score < UNIVERSAL_PROMOTE_CONFIDENCE:
                return False

            # Verificar ≥ 3 áreas distintas en source_area_ids
            source_areas = json.loads(global_pattern.source_area_ids or "[]")
            if len(set(source_areas)) < UNIVERSAL_PROMOTE_MIN_AREAS:
                return False

            # Verificar 2+ ciclos de consolidación (proxy: episode_count ≥ 2)
            if global_pattern.episode_count < UNIVERSAL_PROMOTE_MIN_CYCLES:
                return False

            # Verificar que no existe ya un universal_pattern similar (cosine ≥ 0.75)
            if global_pattern.trigger_embedding:
                trigger_emb = json.loads(global_pattern.trigger_embedding)
                existing_q = await db.execute(
                    select(UniversalPattern)
                    .where(
                        UniversalPattern.trigger_embedding.isnot(None),
                        UniversalPattern.status != "rejected",
                    )
                )
                existing_universals = existing_q.scalars().all()

                for up in existing_universals:
                    try:
                        up_emb = json.loads(up.trigger_embedding)
                        sim = cosine_similarity(trigger_emb, up_emb)
                        if sim >= 0.75:
                            # Ya existe un patrón universal similar — actualizar contadores
                            up.source_tenant_count = max(up.source_tenant_count, 1)
                            up.episode_count += global_pattern.episode_count
                            up.confidence_score = round(
                                (up.confidence_score + global_pattern.confidence_score) / 2, 4
                            )
                            await db.commit()
                            logger.info(
                                f"CME UniversalBrain: patrón universal {up.id} actualizado "
                                f"(similar a global {global_pattern.id})"
                            )
                            return False  # No crear duplicado
                    except Exception:
                        continue

            # Aplicar abstracción de 3 niveles via LLM para eliminar referencias de dominio
            abstract_trigger, abstract_response = await self._apply_abstraction(
                global_pattern.trigger_description,
                global_pattern.response_description,
            )

            if not abstract_trigger or not abstract_response:
                logger.warning(
                    f"CME UniversalBrain: abstracción LLM falló para global_pattern {global_pattern.id}"
                )
                return False

            # Generar embedding del trigger abstracto
            abstract_embedding = await get_embedding(abstract_trigger)

            # Crear registro en universal_patterns con status=pending_approval (Req 36.2)
            universal = UniversalPattern(
                trigger_description=abstract_trigger,
                trigger_embedding=json.dumps(abstract_embedding) if abstract_embedding else None,
                response_description=abstract_response,
                abstraction_level=4,  # nivel universal, por encima de principio=3
                confidence_score=round(global_pattern.confidence_score, 4),
                source_tenant_count=1,
                episode_count=global_pattern.episode_count,
                status="pending_approval",
            )
            db.add(universal)
            await db.commit()
            await db.refresh(universal)

            logger.info(
                f"CME UniversalBrain: nuevo patrón universal {universal.id} creado "
                f"(pending_approval) desde global_pattern {global_pattern.id}"
            )
            return True

        except Exception as e:
            logger.error(f"CME UniversalBrain: error en evaluate_for_promotion: {e}")
            return False

    async def query_universal(
        self,
        query_embedding: list[float],
        db: AsyncSession
    ) -> "UniversalPattern | None":
        """
        Retorna el patrón universal aprobado con cosine ≥ 0.60 más relevante.
        Anotado como "conocimiento universal" en el Context Enricher. (Req 36.4)

        Retorna None si no hay patrones relevantes o si falla.
        """
        try:
            universals_q = await db.execute(
                select(UniversalPattern)
                .where(
                    UniversalPattern.status == "approved",
                    UniversalPattern.trigger_embedding.isnot(None),
                )
            )
            universals = universals_q.scalars().all()

            best: "UniversalPattern | None" = None
            best_sim = UNIVERSAL_QUERY_MIN_SIMILARITY  # umbral mínimo

            for up in universals:
                try:
                    up_emb = json.loads(up.trigger_embedding)
                    sim = cosine_similarity(query_embedding, up_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best = up
                except Exception:
                    continue

            return best

        except Exception as e:
            logger.error(f"CME UniversalBrain: error en query_universal: {e}")
            return None

    async def _apply_abstraction(
        self,
        trigger_description: str,
        response_description: str,
    ) -> tuple[str | None, str | None]:
        """
        Llama al LLM para aplicar abstracción de 3 niveles eliminando toda referencia
        específica de dominio, empresa, industria o identificador. (Req 36.3)

        Retorna (abstract_trigger, abstract_response) o (None, None) si falla.
        """
        prompt = (
            "Eres un sistema de abstracción de conocimiento organizacional. "
            "Tu tarea es reformular el siguiente patrón eliminando COMPLETAMENTE:\n"
            "- Nombres de empresas, departamentos o áreas\n"
            "- Identificadores de usuarios o roles específicos\n"
            "- Terminología propia de una industria concreta\n"
            "- Cualquier dato que permita identificar la fuente\n\n"
            "El resultado debe ser un principio universal aplicable a cualquier organización.\n\n"
            f"TRIGGER ORIGINAL: {trigger_description[:400]}\n"
            f"RESPUESTA ORIGINAL: {response_description[:400]}\n\n"
            "Responde ÚNICAMENTE con un JSON con este formato exacto:\n"
            '{"trigger": "<trigger abstracto ≤300 chars>", "response": "<respuesta abstracta ≤300 chars>"}'
        )

        try:
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

                if res.status_code != 200:
                    return None, None

                content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                content = content.strip()

                # Intentar parsear JSON de la respuesta
                # Buscar el bloque JSON en la respuesta (puede haber texto extra)
                start = content.find("{")
                end = content.rfind("}") + 1
                if start == -1 or end == 0:
                    return None, None

                parsed = json.loads(content[start:end])
                abstract_trigger = parsed.get("trigger", "").strip()[:300]
                abstract_response = parsed.get("response", "").strip()[:300]

                if len(abstract_trigger) < 10 or len(abstract_response) < 10:
                    return None, None

                return abstract_trigger, abstract_response

        except Exception as e:
            logger.warning(f"CME UniversalBrain: error en _apply_abstraction: {e}")
            return None, None


# Instancia global singleton del Universal Brain
universal_brain = UniversalBrain()
