"""
Proactive Intelligence — Genera alertas proactivas para administradores del área.

Detecta:
- Patrones recurrentes con alta confianza (confidence ≥ 0.75, ≥3 sesiones en 7 días)
- Frustración de usuario (frustration_frequency > 0.4)
- Tensión en drives internos del agente (tension > 0.7)
"""
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.cme import (
    AreaPattern,
    AreaEpisode,
    ProactiveAlert,
    UserCognitiveProfile,
    AgentDrive,
)
from app.models.chat import ChatSession
from app.config import settings

logger = logging.getLogger(__name__)

PATTERN_CONFIDENCE_THRESHOLD = 0.75
PATTERN_MIN_SESSIONS = 3
PATTERN_LOOKBACK_DAYS = 7
FRUSTRATION_THRESHOLD = 0.4
DRIVE_TENSION_THRESHOLD = 0.7


class ProactiveIntelligence:

    async def evaluate_patterns(
        self,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> list[ProactiveAlert]:
        """
        Busca patrones con confidence_score ≥ 0.75 disparados en ≥ 3 sesiones
        en los últimos 7 días. Si no existe alerta activa para el mismo patrón
        (o dismissed_until < now): crea ProactiveAlert.
        """
        alerts_created = []
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=PATTERN_LOOKBACK_DAYS)

        try:
            # Obtener patrones aprobados con confidence suficiente
            patterns_q = await db.execute(
                select(AreaPattern)
                .where(
                    AreaPattern.area_id == area_id,
                    AreaPattern.is_approved == True,
                    AreaPattern.confidence_score >= PATTERN_CONFIDENCE_THRESHOLD,
                )
            )
            patterns = patterns_q.scalars().all()

            for pattern in patterns:
                # Contar sesiones distintas en los últimos 7 días que tienen episodios
                # cuyo source_episode_ids incluye episodios del área en ese período
                try:
                    source_ids = json.loads(pattern.source_episode_ids or "[]")
                except Exception:
                    source_ids = []

                if not source_ids:
                    continue

                # Contar episodios del patrón creados en los últimos 7 días
                recent_episodes_q = await db.execute(
                    select(func.count(AreaEpisode.id))
                    .where(
                        AreaEpisode.id.in_(source_ids),
                        AreaEpisode.area_id == area_id,
                        AreaEpisode.created_at >= lookback,
                    )
                )
                recent_count = recent_episodes_q.scalar() or 0

                if recent_count < PATTERN_MIN_SESSIONS:
                    continue

                # Verificar si ya existe alerta activa para este patrón
                existing_alert_q = await db.execute(
                    select(ProactiveAlert)
                    .where(
                        ProactiveAlert.area_id == area_id,
                        ProactiveAlert.pattern_id == pattern.id,
                        ProactiveAlert.status == "active",
                    )
                )
                existing_alert = existing_alert_q.scalar_one_or_none()

                if existing_alert:
                    # Verificar si dismissed_until ya pasó
                    if existing_alert.dismissed_until and existing_alert.dismissed_until > now:
                        continue  # Alerta suprimida, no crear nueva
                    # Si está activa sin dismissed_until, actualizar trigger_count
                    existing_alert.trigger_count = recent_count
                    await db.commit()
                    continue

                # Generar mensaje y acción sugerida via LLM
                alert_message, suggested_action = await self._generate_pattern_alert(
                    pattern, recent_count
                )

                alert = ProactiveAlert(
                    area_id=area_id,
                    tenant_id=tenant_id,
                    pattern_id=pattern.id,
                    alert_message=alert_message[:300],
                    trigger_count=recent_count,
                    suggested_action=suggested_action[:300] if suggested_action else None,
                    status="active",
                )
                db.add(alert)
                alerts_created.append(alert)

            if alerts_created:
                await db.commit()
                logger.info(
                    f"CME ProactiveIntelligence: {len(alerts_created)} alertas de patrón "
                    f"creadas para área {area_id}"
                )

        except Exception as e:
            logger.error(f"CME ProactiveIntelligence: error en evaluate_patterns: {e}")

        return alerts_created

    async def evaluate_user_frustration(
        self,
        user_id: str,
        area_id: str,
        db: AsyncSession
    ) -> "ProactiveAlert | None":
        """
        Si frustration_frequency del perfil cognitivo > 0.4, genera alerta de soporte.
        Req 26.4
        """
        try:
            profile_q = await db.execute(
                select(UserCognitiveProfile)
                .where(
                    UserCognitiveProfile.user_id == user_id,
                    UserCognitiveProfile.area_id == area_id,
                )
            )
            profile = profile_q.scalar_one_or_none()

            if not profile:
                return None

            if profile.frustration_frequency <= FRUSTRATION_THRESHOLD:
                return None

            # Verificar si ya existe alerta activa de frustración para este usuario
            existing_q = await db.execute(
                select(ProactiveAlert)
                .where(
                    ProactiveAlert.area_id == area_id,
                    ProactiveAlert.pattern_id.is_(None),
                    ProactiveAlert.status == "active",
                    ProactiveAlert.alert_message.contains(user_id[:8]),
                )
            )
            if existing_q.scalar_one_or_none():
                return None

            frustration_pct = round(profile.frustration_frequency * 100)
            alert_message = (
                f"Usuario con alta frecuencia de frustración ({frustration_pct}%). "
                f"Puede necesitar soporte adicional o ajuste en las respuestas del área."
            )[:300]

            suggested_action = (
                "Revisar las últimas sesiones del usuario y considerar ajustar el nivel de detalle "
                "o la metodología de respuesta para este perfil."
            )[:300]

            alert = ProactiveAlert(
                area_id=area_id,
                tenant_id=profile.tenant_id,
                pattern_id=None,
                alert_message=alert_message,
                trigger_count=1,
                suggested_action=suggested_action,
                status="active",
            )
            db.add(alert)
            await db.commit()
            logger.info(
                f"CME ProactiveIntelligence: alerta de frustración creada para usuario {user_id}"
            )
            return alert

        except Exception as e:
            logger.error(f"CME ProactiveIntelligence: error en evaluate_user_frustration: {e}")
            return None

    async def evaluate_drives(
        self,
        area_id: str,
        db: AsyncSession
    ) -> list[ProactiveAlert]:
        """
        Si tension de cualquier drive > 0.7, genera alerta describiendo
        qué quiere resolver el sistema. Req 27.3
        """
        alerts_created = []
        now = datetime.now(timezone.utc)

        try:
            drives_q = await db.execute(
                select(AgentDrive)
                .where(
                    AgentDrive.area_id == area_id,
                    AgentDrive.tension > DRIVE_TENSION_THRESHOLD,
                )
            )
            drives = drives_q.scalars().all()

            for drive in drives:
                # Verificar si ya existe alerta activa para este drive
                drive_marker = f"drive:{drive.drive_type}"
                existing_q = await db.execute(
                    select(ProactiveAlert)
                    .where(
                        ProactiveAlert.area_id == area_id,
                        ProactiveAlert.status == "active",
                        ProactiveAlert.alert_message.contains(drive_marker),
                    )
                )
                if existing_q.scalar_one_or_none():
                    continue

                alert_message, suggested_action = self._build_drive_alert(drive)

                alert = ProactiveAlert(
                    area_id=area_id,
                    tenant_id=drive.tenant_id,
                    pattern_id=None,
                    alert_message=alert_message[:300],
                    trigger_count=1,
                    suggested_action=suggested_action[:300],
                    status="active",
                )
                db.add(alert)
                alerts_created.append(alert)

            if alerts_created:
                await db.commit()
                logger.info(
                    f"CME ProactiveIntelligence: {len(alerts_created)} alertas de drive "
                    f"creadas para área {area_id}"
                )

        except Exception as e:
            logger.error(f"CME ProactiveIntelligence: error en evaluate_drives: {e}")

        return alerts_created

    def _build_drive_alert(self, drive: AgentDrive) -> tuple[str, str]:
        """Construye mensaje y acción sugerida para una alerta de drive."""
        tension_pct = round(drive.tension * 100)

        drive_descriptions = {
            "gap_reduction": (
                f"[drive:gap_reduction] El sistema detecta alta tensión ({tension_pct}%) "
                f"en reducción de brechas de conocimiento. Hay gaps sin resolver que afectan "
                f"la calidad de las respuestas.",
                "Revisar los knowledge gaps pendientes y considerar agregar documentación "
                "o ejemplos que cubran los temas con mayor frecuencia de aparición."
            ),
            "quality_maximization": (
                f"[drive:quality_maximization] El sistema detecta alta tensión ({tension_pct}%) "
                f"en maximización de calidad. Las sesiones recientes muestran scores por debajo "
                f"del objetivo.",
                "Revisar los patrones de baja calidad y considerar actualizar las metodologías "
                "del área o el contexto del sistema."
            ),
            "coherence_maintenance": (
                f"[drive:coherence_maintenance] El sistema detecta alta tensión ({tension_pct}%) "
                f"en mantenimiento de coherencia. Existen contradicciones sin resolver entre "
                f"patrones del área.",
                "Revisar las contradicciones pendientes en el Brain Control Panel y resolver "
                "cuál patrón debe prevalecer."
            ),
        }

        default_msg = (
            f"[drive:{drive.drive_type}] Alta tensión ({tension_pct}%) detectada en el "
            f"drive {drive.drive_type}. Valor actual: {drive.current_value:.2f}, "
            f"objetivo: {drive.target_value:.2f}."
        )
        default_action = (
            f"Revisar el estado del drive {drive.drive_type} en el panel de administración."
        )

        msg, action = drive_descriptions.get(drive.drive_type, (default_msg, default_action))
        return msg, action

    async def _generate_pattern_alert(
        self,
        pattern: AreaPattern,
        trigger_count: int
    ) -> tuple[str, str | None]:
        """Genera mensaje de alerta y acción sugerida via LLM para un patrón recurrente."""
        # Fallback determinístico
        fallback_message = (
            f"Patrón recurrente detectado: '{pattern.trigger_description[:150]}' "
            f"se ha activado {trigger_count} veces en los últimos 7 días "
            f"(confianza: {pattern.confidence_score:.0%})."
        )[:300]

        fallback_action = (
            f"Revisar el patrón y considerar si la respuesta actual es óptima: "
            f"'{pattern.response_description[:100]}'"
        )[:300]

        try:
            prompt = (
                f"Un patrón del área se ha activado {trigger_count} veces en 7 días.\n"
                f"Patrón: {pattern.trigger_description[:200]}\n"
                f"Respuesta actual: {pattern.response_description[:200]}\n"
                f"Confianza: {pattern.confidence_score:.0%}\n\n"
                f"Genera en JSON:\n"
                f'{{"alert": "mensaje de alerta para el admin (≤300 chars)", '
                f'"action": "acción sugerida (≤300 chars)"}}\n'
                f"Solo JSON, sin texto adicional."
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
                    if "```" in content:
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                    data = json.loads(content.strip())
                    alert_msg = data.get("alert", fallback_message)[:300]
                    action_msg = data.get("action", fallback_action)[:300]
                    return alert_msg, action_msg
        except Exception as e:
            logger.debug(f"CME ProactiveIntelligence: LLM falló para alerta de patrón: {e}")

        return fallback_message, fallback_action


# Instancia global singleton
proactive_intelligence = ProactiveIntelligence()
