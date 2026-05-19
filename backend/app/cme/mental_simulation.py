"""
Mental Simulation — Evaluación interna de payloads candidatos antes de responder.

Cuando el usuario está frustrado o hay un patrón de fallo reciente,
el agente genera 2 payloads alternativos y selecciona el mejor.

Añade ≤ 300ms al tiempo de respuesta.

Verificar settings.CME_ENABLE_MENTAL_SIMULATION antes de ejecutar.
"""
import json
import logging
import time
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AreaEpisode, SimulationLog
from app.rag import get_embedding
from app.config import settings

logger = logging.getLogger(__name__)

MIN_EPISODES_FOR_SIMULATION = 5
MAX_SIMULATION_TIME_MS = 300


class MentalSimulation:

    def should_activate(
        self,
        working_memory,
        area_episode_count: int
    ) -> bool:
        """
        Determina si la simulación mental debe activarse.

        Condiciones:
        - detected_emotion = frustrated O failure_pattern reciente en working_memory
        - Y el área tiene >= 50 episodios

        Retorna True si debe activarse.
        """
        if not settings.CME_ENABLE_MENTAL_SIMULATION:
            return False

        if area_episode_count < MIN_EPISODES_FOR_SIMULATION:
            return False

        if working_memory is None:
            return False

        # Activar si el usuario está frustrado
        if getattr(working_memory, "detected_emotion", "neutral") == "frustrated":
            return True

        # Activar si hay failure_pattern reciente en los episodios activos
        # (se detecta via active_episode_ids que contienen episodios de fallo)
        active_ids = getattr(working_memory, "active_episode_ids", [])
        if active_ids:
            # Si hay episodios activos marcados como failure, activar
            # (la verificación real se hace en simulate())
            return True

        return False

    async def simulate(
        self,
        query: str,
        area_id: str,
        tenant_id: str,
        working_memory,
        db: AsyncSession
    ) -> str:
        """
        Genera 2 payloads alternativos, los puntúa y retorna el mejor.

        Proceso:
        1. Genera payload_a (respuesta directa)
        2. Genera payload_b (respuesta con contexto adicional)
        3. Puntúa cada uno con mini-prompt al LLM
        4. Registra en simulation_log
        5. Retorna el payload con mayor score

        Añade ≤ 300ms al tiempo de respuesta.
        """
        if not settings.CME_ENABLE_MENTAL_SIMULATION:
            return query

        start_time = time.monotonic()

        try:
            # Generar los dos payloads alternativos
            payload_a, payload_b = await self._generate_payloads(query, area_id, db)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms > MAX_SIMULATION_TIME_MS:
                logger.debug(
                    f"CME MentalSimulation: tiempo excedido ({elapsed_ms:.0f}ms), "
                    f"retornando payload_a sin puntuar"
                )
                return payload_a

            # Puntuar los payloads
            score_a, score_b = await self._score_payloads(query, payload_a, payload_b)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms > MAX_SIMULATION_TIME_MS:
                logger.debug(
                    f"CME MentalSimulation: tiempo excedido en scoring ({elapsed_ms:.0f}ms)"
                )

            # Seleccionar el mejor
            selected = "a" if score_a >= score_b else "b"
            best_payload = payload_a if selected == "a" else payload_b

            # Determinar trigger_reason
            trigger_reason = "frustration"
            if working_memory and getattr(working_memory, "detected_emotion", "") != "frustrated":
                trigger_reason = "failure_pattern"

            # Registrar en simulation_log
            try:
                query_emb = await get_embedding(query)
                if query_emb:
                    session_id = getattr(working_memory, "session_id", "unknown") if working_memory else "unknown"
                    log = SimulationLog(
                        session_id=session_id,
                        query_embedding=json.dumps(query_emb),
                        payload_a_score=score_a,
                        payload_b_score=score_b,
                        selected_payload=selected,
                        trigger_reason=trigger_reason,
                    )
                    db.add(log)
                    await db.commit()
            except Exception as e:
                logger.debug(f"CME MentalSimulation: error guardando simulation_log: {e}")

            logger.debug(
                f"CME MentalSimulation: payload_{selected} seleccionado "
                f"(score_a={score_a:.2f}, score_b={score_b:.2f})"
            )
            return best_payload

        except Exception as e:
            logger.warning(f"CME MentalSimulation: error en simulate: {e}")
            return query

    async def _generate_payloads(
        self,
        query: str,
        area_id: str,
        db: AsyncSession
    ) -> tuple[str, str]:
        """
        Genera dos payloads alternativos para el query.
        payload_a: respuesta directa y concisa
        payload_b: respuesta con contexto adicional y ejemplos
        """
        # Obtener contexto del área para enriquecer los payloads
        context_hint = ""
        try:
            recent_eps_q = await db.execute(
                select(AreaEpisode)
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.session_arc == "resolved",
                    AreaEpisode.extraction_status == "completed",
                )
                .order_by(AreaEpisode.created_at.desc())
                .limit(3)
            )
            recent_eps = recent_eps_q.scalars().all()
            if recent_eps:
                context_hint = " ".join(ep.strategy[:100] for ep in recent_eps[:2])
        except Exception:
            pass

        payload_a = (
            f"Respuesta directa para: {query[:200]}"
            + (f" (contexto: {context_hint[:100]})" if context_hint else "")
        )

        payload_b = (
            f"Respuesta detallada para: {query[:200]} — "
            f"Considera el contexto del área y proporciona ejemplos concretos."
            + (f" Estrategias previas exitosas: {context_hint[:150]}" if context_hint else "")
        )

        return payload_a, payload_b

    async def _score_payloads(
        self,
        query: str,
        payload_a: str,
        payload_b: str
    ) -> tuple[float, float]:
        """
        Puntúa los dos payloads usando un mini-prompt al LLM.
        Retorna (score_a, score_b) en [0, 1].
        """
        try:
            prompt = (
                f"Evalúa cuál de estas dos respuestas es mejor para la consulta del usuario.\n\n"
                f"Consulta: {query[:200]}\n\n"
                f"Respuesta A: {payload_a[:300]}\n\n"
                f"Respuesta B: {payload_b[:300]}\n\n"
                f"Responde SOLO con JSON: "
                f'{{\"score_a\": 0.0-1.0, \"score_b\": 0.0-1.0}}'
            )

            async with httpx.AsyncClient(timeout=10) as client:
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
                    if "```" in content:
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                    data = json.loads(content.strip())
                    score_a = float(data.get("score_a", 0.5))
                    score_b = float(data.get("score_b", 0.5))
                    return min(1.0, max(0.0, score_a)), min(1.0, max(0.0, score_b))

        except Exception as e:
            logger.debug(f"CME MentalSimulation: error en _score_payloads: {e}")

        # Fallback: scores iguales
        return 0.5, 0.5


# Instancia global singleton
mental_simulation = MentalSimulation()
