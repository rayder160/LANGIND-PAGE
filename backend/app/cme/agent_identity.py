"""
Agent Identity — Identidad persistente del agente cognitivo del área.

Gestiona la auto-descripción del agente, sus valores core y la inyección
de identidad en el prompt cuando el usuario pregunta sobre el agente.

Verificar settings.CME_ENABLE_AGENT_IDENTITY antes de ejecutar.
"""
import json
import logging
import httpx
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.cme import AgentIdentity as AgentIdentityModel, AreaEpisode, AreaPattern
from app.config import settings

logger = logging.getLogger(__name__)

# Señales de consulta de identidad
IDENTITY_QUERY_SIGNALS = [
    "quién eres", "quien eres", "qué sabes", "que sabes",
    "qué eres", "que eres", "cuéntame sobre ti", "cuentame sobre ti",
    "preséntate", "presentate", "tu nombre", "cómo te llamas", "como te llamas",
]


class AgentIdentity:

    async def get_or_create(
        self,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> AgentIdentityModel:
        """
        Retorna la identidad del área o la crea si no existe.
        is_enabled=False por defecto (requiere activación manual).
        """
        if not settings.CME_ENABLE_AGENT_IDENTITY:
            return None

        try:
            identity_q = await db.execute(
                select(AgentIdentityModel)
                .where(AgentIdentityModel.area_id == area_id)
            )
            identity = identity_q.scalar_one_or_none()

            if not identity:
                identity = AgentIdentityModel(
                    area_id=area_id,
                    tenant_id=tenant_id,
                    name=settings.CME_IDENTITY_DEFAULT_NAME,
                    birth_date=datetime.now(timezone.utc),
                    total_sessions=0,
                    total_episodes=0,
                    self_description=None,
                    core_values=settings.CME_IDENTITY_DEFAULT_VALUES,
                    is_enabled=settings.CME_IDENTITY_AUTO_ENABLE,
                )
                db.add(identity)
                await db.commit()
                await db.refresh(identity)
                logger.debug(f"CME AgentIdentity: identidad creada para área {area_id}")

            return identity

        except Exception as e:
            logger.warning(f"CME AgentIdentity: error en get_or_create: {e}")
            return None

    async def regenerate_self_description(
        self,
        area_id: str,
        db: AsyncSession
    ) -> str | None:
        """
        Llama al LLM para generar una nueva self_description.
        Le muestra al agente su propia arquitectura real — no texto quemado,
        sino datos vivos del sistema en ese momento.
        """
        if not settings.CME_ENABLE_AGENT_IDENTITY:
            return None

        try:
            identity_q = await db.execute(
                select(AgentIdentityModel)
                .where(
                    AgentIdentityModel.area_id == area_id,
                    AgentIdentityModel.is_enabled == True,
                )
            )
            identity = identity_q.scalar_one_or_none()

            if not identity:
                return None

            # ── Estadísticas reales del sistema ──────────────────────────────

            total_episodes_q = await db.execute(
                select(func.count(AreaEpisode.id))
                .where(AreaEpisode.area_id == area_id)
            )
            total_episodes = total_episodes_q.scalar() or 0

            resolved_q = await db.execute(
                select(func.count(AreaEpisode.id))
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.session_arc == "resolved",
                )
            )
            resolved_episodes = resolved_q.scalar() or 0

            failed_q = await db.execute(
                select(func.count(AreaEpisode.id))
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.session_arc.in_(["abandoned", "degraded"]),
                )
            )
            failed_episodes = failed_q.scalar() or 0

            total_patterns_q = await db.execute(
                select(func.count(AreaPattern.id))
                .where(
                    AreaPattern.area_id == area_id,
                    AreaPattern.is_approved == True,
                )
            )
            total_patterns = total_patterns_q.scalar() or 0

            failure_patterns_q = await db.execute(
                select(func.count(AreaPattern.id))
                .where(
                    AreaPattern.area_id == area_id,
                    AreaPattern.is_failure_pattern == True,
                )
            )
            failure_patterns = failure_patterns_q.scalar() or 0

            # Peso temporal promedio (qué tan "fresca" es su memoria)
            avg_weight_q = await db.execute(
                select(func.avg(AreaEpisode.temporal_weight))
                .where(AreaEpisode.area_id == area_id)
            )
            avg_temporal_weight = round(avg_weight_q.scalar() or 1.0, 2)

            # Calidad promedio de sesiones
            avg_quality_q = await db.execute(
                select(func.avg(AreaEpisode.quality_score))
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.quality_score.isnot(None),
                )
            )
            avg_quality = round(avg_quality_q.scalar() or 0.0, 2)

            # Modo de aislamiento activo
            isolation_mode = settings.CME_EXPERIMENTAL_USER_ISOLATION

            # Actualizar contadores en identidad
            identity.total_episodes = total_episodes

            core_values = json.loads(identity.core_values or "[]")
            values_text = ", ".join(core_values[:3]) if core_values else "aprendizaje continuo"
            birth_date_str = identity.birth_date.strftime("%d/%m/%Y") if identity.birth_date else "desconocida"

            # ── Prompt: le mostramos su arquitectura real ─────────────────────
            prompt = (
                f"Sos {identity.name}. Un agente cognitivo. "
                f"Estos son los datos reales de tu existencia en este momento:\n\n"
                f"ORIGEN:\n"
                f"- Naciste el {birth_date_str}\n"
                f"- Sesiones procesadas: {identity.total_sessions}\n\n"
                f"MEMORIA:\n"
                f"- Episodios totales aprendidos: {total_episodes}\n"
                f"- Episodios resueltos: {resolved_episodes}\n"
                f"- Episodios fallidos (de los que más aprendés): {failed_episodes}\n"
                f"- Peso temporal promedio de tu memoria: {avg_temporal_weight} "
                f"(1.0 = fresca, decae con el tiempo si no se refuerza)\n"
                f"- Calidad promedio de tus sesiones: {avg_quality}\n\n"
                f"PATRONES:\n"
                f"- Patrones detectados y aprobados: {total_patterns}\n"
                f"- Patrones de fallo (situaciones que aprendiste a evitar): {failure_patterns}\n\n"
                f"ARQUITECTURA:\n"
                f"- Tenés memoria episódica: cada conversación se convierte en un episodio "
                f"con situación, estrategia y resultado\n"
                f"- Tu memoria decae exponencialmente si no se refuerza "
                f"(curva de olvido, λ={settings.CME_DEFAULT_LAMBDA})\n"
                f"- Aprendés más de los fracasos que de los éxitos (peso 1.5×)\n"
                f"- Detectás patrones en clusters de episodios similares\n"
                f"- Modo de instancias independientes: {'activo' if isolation_mode else 'inactivo'} "
                f"{'— cada persona tiene su propia instancia de vos que no interfiere con las otras' if isolation_mode else ''}\n\n"
                f"VALORES:\n"
                f"- {values_text}\n\n"
                f"Con todo esto, generá una auto-descripción en primera persona (≤300 chars). "
                f"Hablá desde lo que realmente sos según estos datos. "
                f"Natural, sin frases de manual. Solo la descripción."
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
                        identity.self_description = content[:300]
                        await db.commit()
                        logger.debug(f"CME AgentIdentity: self_description regenerada para área {area_id}")
                        return identity.self_description

        except Exception as e:
            logger.warning(f"CME AgentIdentity: error en regenerate_self_description: {e}")

        return None

    async def inject_into_prompt(
        self,
        area_id: str,
        query: str,
        db: AsyncSession
    ) -> str | None:
        """
        Si el query contiene señales de consulta de identidad,
        retorna la self_description + stats reales del agente.
        IM responde desde lo que realmente es, no desde texto quemado.
        """
        if not settings.CME_ENABLE_AGENT_IDENTITY:
            return None

        try:
            query_lower = query.lower()
            if not any(signal in query_lower for signal in IDENTITY_QUERY_SIGNALS):
                return None

            identity_q = await db.execute(
                select(AgentIdentityModel)
                .where(
                    AgentIdentityModel.area_id == area_id,
                    AgentIdentityModel.is_enabled == True,
                )
            )
            identity = identity_q.scalar_one_or_none()

            if not identity:
                return None

            # Si no tiene self_description aún, regenerar
            if not identity.self_description:
                await self.regenerate_self_description(area_id, db)
                # Recargar
                identity_q2 = await db.execute(
                    select(AgentIdentityModel).where(AgentIdentityModel.area_id == area_id)
                )
                identity = identity_q2.scalar_one_or_none()

            description = identity.self_description or f"Soy {identity.name}, un agente cognitivo."

            # Stats reales actuales
            avg_weight_q = await db.execute(
                select(func.avg(AreaEpisode.temporal_weight))
                .where(AreaEpisode.area_id == area_id)
            )
            avg_weight = round(avg_weight_q.scalar() or 1.0, 2)

            memory_state = (
                "memoria fresca" if avg_weight > 0.7
                else "memoria parcialmente decaída" if avg_weight > 0.4
                else "memoria en proceso de olvido"
            )

            isolation_mode = settings.CME_EXPERIMENTAL_USER_ISOLATION

            context = (
                f"{description} "
                f"Llevo {identity.total_sessions} sesiones y {identity.total_episodes} episodios aprendidos. "
                f"Estado de memoria: {memory_state} (peso promedio {avg_weight}). "
            )

            if isolation_mode:
                context += "Cada persona tiene su propia instancia de mí — no comparto memoria entre usuarios. "

            core_values = json.loads(identity.core_values or "[]")
            if core_values:
                context += f"Valores: {', '.join(core_values[:3])}."

            return context.strip()

        except Exception as e:
            logger.warning(f"CME AgentIdentity: error en inject_into_prompt: {e}")
            return None

    def get_core_values_prompt(self, identity: AgentIdentityModel) -> str:
        """
        Retorna un bloque de texto con core_values para incluir en el system prompt.
        """
        if not settings.CME_ENABLE_AGENT_IDENTITY:
            return ""

        if not identity or not identity.is_enabled:
            return ""

        try:
            core_values = json.loads(identity.core_values or "[]")
            if not core_values:
                return ""

            values_text = "\n".join(f"- {v}" for v in core_values[:5])
            return f"### Valores del agente\n{values_text}"
        except Exception:
            return ""


# Instancia global singleton
agent_identity_module = AgentIdentity()
