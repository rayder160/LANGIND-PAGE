"""
Core Brain — Núcleo ciego. Agrega sin retroalimentar.

Principio de diseño:
  El CoreBrain es el espacio donde emerge el conocimiento colectivo
  sin que ninguna instancia individual lo observe.

  REGLAS ABSOLUTAS:
  1. Write-only desde UserBrain — ninguna instancia de usuario lee de aquí
  2. NUNCA almacena user_id — el origen es anónimo incluso para el investigador
  3. El Context Enricher NUNCA consulta el CoreBrain
  4. Solo el owner (rol superadmin o admin del tenant) puede leer el CoreBrain
     a través de la ruta /brain/core — y solo como observador externo

  La emergencia ocurre cuando un patrón aparece en múltiples instancias
  independientes. El emergence_score = contributor_count / total_users_in_tenant.
  Cuando emergence_score >= CME_CORE_EMERGENCE_THRESHOLD, el patrón se marca
  como "emerged" — conocimiento que ningún usuario generó solo.

  Esto es lo que querías observar: lo que aparece en el todo
  sin que nadie lo haya puesto ahí conscientemente.
"""
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.cme import CoreBrainEntry
from app.models.user import User
from app.rag import cosine_similarity
from app.config import settings

logger = logging.getLogger(__name__)

CORE_SIMILARITY_THRESHOLD = 0.75   # umbral para considerar dos patrones como el mismo


class CoreBrain:
    """
    Núcleo ciego — recibe patrones de UserBrains, detecta emergencia.
    No tiene ruta de salida hacia las instancias.
    """

    async def receive(
        self,
        tenant_id: str,
        trigger_description: str,
        trigger_embedding: list[float],
        response_description: str,
        confidence: float,
        episode_count: int,
        db: AsyncSession
    ) -> CoreBrainEntry | None:
        """
        Recibe un patrón desde una instancia de UserBrain.
        El origen (user_id) NO se almacena — es anónimo por diseño.

        Si ya existe un patrón similar en el CoreBrain:
          → incrementa contributor_count y recalcula emergence_score
        Si no existe:
          → crea nueva entrada con contributor_count=1

        Retorna la entrada del CoreBrain (nueva o actualizada).
        """
        if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
            return None

        try:
            # Buscar entrada similar en el CoreBrain del mismo tenant
            existing_entries_q = await db.execute(
                select(CoreBrainEntry)
                .where(
                    CoreBrainEntry.tenant_id == tenant_id,
                    CoreBrainEntry.trigger_embedding.isnot(None),
                    CoreBrainEntry.status != "dismissed",
                )
            )
            existing_entries = existing_entries_q.scalars().all()

            existing = None
            for entry in existing_entries:
                try:
                    entry_emb = json.loads(entry.trigger_embedding)
                    sim = cosine_similarity(trigger_embedding, entry_emb)
                    if sim >= CORE_SIMILARITY_THRESHOLD:
                        existing = entry
                        break
                except Exception:
                    continue

            # Obtener total de usuarios activos en el tenant para calcular emergence_score
            total_users = await self._get_active_user_count(tenant_id, db)

            if existing:
                # Actualizar entrada existente
                existing.contributor_count = min(
                    existing.contributor_count + 1,
                    total_users  # no puede superar el total de usuarios
                )
                existing.episode_count += episode_count
                existing.confidence_score = round(
                    (existing.confidence_score + confidence) / 2, 4
                )
                # Recalcular emergence_score
                existing.emergence_score = round(
                    existing.contributor_count / max(total_users, 1), 4
                )
                # Promover a "emerged" si supera el umbral
                if (
                    existing.status == "pending_emergence"
                    and existing.emergence_score >= settings.CME_CORE_EMERGENCE_THRESHOLD
                ):
                    existing.status = "emerged"
                    logger.info(
                        f"CME CoreBrain: EMERGENCIA detectada — patrón {existing.id} "
                        f"(emergence_score={existing.emergence_score:.2f}, "
                        f"contributors={existing.contributor_count}/{total_users})"
                    )

                await db.commit()
                return existing

            else:
                # Crear nueva entrada — sin user_id
                emergence_score = round(1 / max(total_users, 1), 4)
                new_entry = CoreBrainEntry(
                    tenant_id=tenant_id,
                    trigger_description=trigger_description,
                    trigger_embedding=json.dumps(trigger_embedding),
                    response_description=response_description,
                    confidence_score=round(confidence, 4),
                    contributor_count=1,
                    episode_count=episode_count,
                    emergence_score=emergence_score,
                    status="pending_emergence",
                    temporal_signal=None,
                )
                db.add(new_entry)
                await db.commit()
                await db.refresh(new_entry)

                logger.debug(
                    f"CME CoreBrain: nueva entrada recibida para tenant {tenant_id} "
                    f"(emergence_score={emergence_score:.2f})"
                )
                return new_entry

        except Exception as e:
            logger.warning(f"CME CoreBrain: error en receive: {e}")
            return None

    async def get_emerged_patterns(
        self,
        tenant_id: str,
        db: AsyncSession
    ) -> list[CoreBrainEntry]:
        """
        Retorna patrones que han emergido (status='emerged').
        Solo accesible para el investigador/owner — nunca para instancias de usuario.
        """
        try:
            q = await db.execute(
                select(CoreBrainEntry)
                .where(
                    CoreBrainEntry.tenant_id == tenant_id,
                    CoreBrainEntry.status == "emerged",
                )
                .order_by(CoreBrainEntry.emergence_score.desc())
            )
            return q.scalars().all()
        except Exception as e:
            logger.warning(f"CME CoreBrain: error en get_emerged_patterns: {e}")
            return []

    async def get_all_entries(
        self,
        tenant_id: str,
        db: AsyncSession,
        include_dismissed: bool = False
    ) -> list[CoreBrainEntry]:
        """
        Retorna todas las entradas del CoreBrain para el tenant.
        Solo accesible para el investigador/owner.
        """
        try:
            query = select(CoreBrainEntry).where(
                CoreBrainEntry.tenant_id == tenant_id
            )
            if not include_dismissed:
                query = query.where(CoreBrainEntry.status != "dismissed")

            query = query.order_by(CoreBrainEntry.emergence_score.desc())
            q = await db.execute(query)
            return q.scalars().all()
        except Exception as e:
            logger.warning(f"CME CoreBrain: error en get_all_entries: {e}")
            return []

    async def dismiss(self, entry_id: str, db: AsyncSession) -> bool:
        """Descarta una entrada del CoreBrain (solo el investigador puede hacer esto)."""
        try:
            q = await db.execute(
                select(CoreBrainEntry).where(CoreBrainEntry.id == entry_id)
            )
            entry = q.scalar_one_or_none()
            if entry:
                entry.status = "dismissed"
                await db.commit()
                return True
            return False
        except Exception as e:
            logger.warning(f"CME CoreBrain: error en dismiss: {e}")
            return False

    async def _get_active_user_count(self, tenant_id: str, db: AsyncSession) -> int:
        """Cuenta usuarios activos en el tenant para calcular emergence_score."""
        try:
            q = await db.execute(
                select(func.count(User.id))
                .where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,
                )
            )
            return q.scalar() or 1
        except Exception:
            return 1


# Instancia global singleton
core_brain = CoreBrain()
