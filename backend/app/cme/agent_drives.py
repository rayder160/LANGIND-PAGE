"""
Agent Drives — Drives internos del agente cognitivo (tensión hacia objetivos).

Tres drives:
- gap_reduction: reducir brechas de conocimiento
- quality_maximization: maximizar calidad de respuestas
- coherence_maintenance: mantener coherencia entre patrones

Verificar settings.CME_ENABLE_AGENT_DRIVES antes de ejecutar.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.cme import AgentDrive, ProactiveAlert
from app.config import settings

logger = logging.getLogger(__name__)

DRIVE_TYPES = ["gap_reduction", "quality_maximization", "coherence_maintenance"]
TENSION_ALERT_THRESHOLD = 0.7


class AgentDrives:

    async def initialize_for_area(
        self,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> list[AgentDrive]:
        """
        Crea los 3 drives para el área si no existen.
        Retorna la lista de drives (existentes o recién creados).
        """
        if not settings.CME_ENABLE_AGENT_DRIVES:
            return []

        drives = []
        for drive_type in DRIVE_TYPES:
            existing_q = await db.execute(
                select(AgentDrive)
                .where(
                    AgentDrive.area_id == area_id,
                    AgentDrive.drive_type == drive_type,
                )
            )
            existing = existing_q.scalar_one_or_none()

            if existing:
                drives.append(existing)
            else:
                drive = AgentDrive(
                    area_id=area_id,
                    tenant_id=tenant_id,
                    drive_type=drive_type,
                    current_value=0.0,
                    target_value=1.0,
                    tension=0.0,
                    is_enabled=True,
                )
                db.add(drive)
                drives.append(drive)

        await db.commit()
        logger.debug(f"CME AgentDrives: drives inicializados para área {area_id}")
        return drives

    async def update_after_session(
        self,
        area_id: str,
        session_arc: str,
        quality_score: float | None,
        db: AsyncSession
    ) -> None:
        """
        Actualiza la tensión de los drives después de una sesión.

        gap_reduction:
          +0.1 si arc = abandoned/degraded
          -0.03 si arc = resolved

        quality_maximization:
          +0.15 si quality < 0.6
          -0.05 si quality >= 0.8

        coherence_maintenance: no cambia aquí (cambia cuando se detectan contradicciones)
        """
        if not settings.CME_ENABLE_AGENT_DRIVES:
            return

        try:
            drives_q = await db.execute(
                select(AgentDrive)
                .where(
                    AgentDrive.area_id == area_id,
                    AgentDrive.is_enabled == True,
                )
            )
            drives = drives_q.scalars().all()

            for drive in drives:
                if drive.drive_type == "gap_reduction":
                    if session_arc in ("abandoned", "degraded"):
                        drive.tension = min(1.0, drive.tension + 0.1)
                    elif session_arc == "resolved":
                        drive.tension = max(0.0, drive.tension - 0.03)

                elif drive.drive_type == "quality_maximization":
                    if quality_score is not None:
                        if quality_score < 0.6:
                            drive.tension = min(1.0, drive.tension + 0.15)
                        elif quality_score >= 0.8:
                            drive.tension = max(0.0, drive.tension - 0.05)

                # coherence_maintenance no cambia aquí

            await db.commit()
            logger.debug(
                f"CME AgentDrives: tensión actualizada para área {area_id} "
                f"(arc={session_arc}, quality={quality_score})"
            )

        except Exception as e:
            logger.warning(f"CME AgentDrives: error en update_after_session: {e}")

    async def check_tension_alerts(
        self,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> list[ProactiveAlert]:
        """
        Si tension > 0.7 para cualquier drive, crea ProactiveAlert.
        Retorna lista de alertas creadas.
        """
        if not settings.CME_ENABLE_AGENT_DRIVES:
            return []

        alerts_created = []
        try:
            drives_q = await db.execute(
                select(AgentDrive)
                .where(
                    AgentDrive.area_id == area_id,
                    AgentDrive.tension > TENSION_ALERT_THRESHOLD,
                    AgentDrive.is_enabled == True,
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

                tension_pct = round(drive.tension * 100)
                alert_message = (
                    f"[drive:{drive.drive_type}] Alta tensión ({tension_pct}%) detectada. "
                    f"El agente necesita atención en: {drive.drive_type.replace('_', ' ')}."
                )[:300]

                suggested_action = _get_drive_suggested_action(drive.drive_type)

                alert = ProactiveAlert(
                    area_id=area_id,
                    tenant_id=tenant_id,
                    pattern_id=None,
                    alert_message=alert_message,
                    trigger_count=1,
                    suggested_action=suggested_action,
                    status="active",
                )
                db.add(alert)
                alerts_created.append(alert)

            if alerts_created:
                await db.commit()
                logger.info(
                    f"CME AgentDrives: {len(alerts_created)} alertas de tensión "
                    f"creadas para área {area_id}"
                )

        except Exception as e:
            logger.warning(f"CME AgentDrives: error en check_tension_alerts: {e}")

        return alerts_created

    async def reset_tension(self, drive_id: str, db: AsyncSession) -> None:
        """Resetea la tensión de un drive a 0."""
        if not settings.CME_ENABLE_AGENT_DRIVES:
            return

        try:
            drive_q = await db.execute(
                select(AgentDrive).where(AgentDrive.id == drive_id)
            )
            drive = drive_q.scalar_one_or_none()
            if drive:
                drive.tension = 0.0
                await db.commit()
                logger.debug(f"CME AgentDrives: tensión reseteada para drive {drive_id}")
        except Exception as e:
            logger.warning(f"CME AgentDrives: error en reset_tension: {e}")


def _get_drive_suggested_action(drive_type: str) -> str:
    """Retorna la acción sugerida para un tipo de drive."""
    actions = {
        "gap_reduction": (
            "Revisar los knowledge gaps pendientes y agregar documentación "
            "o ejemplos que cubran los temas con mayor frecuencia de aparición."
        ),
        "quality_maximization": (
            "Revisar los patrones de baja calidad y actualizar las metodologías "
            "del área o el contexto del sistema."
        ),
        "coherence_maintenance": (
            "Revisar las contradicciones pendientes en el Brain Control Panel "
            "y resolver cuál patrón debe prevalecer."
        ),
    }
    return actions.get(drive_type, f"Revisar el estado del drive {drive_type}.")[:300]


# Instancia global singleton
agent_drives = AgentDrives()
