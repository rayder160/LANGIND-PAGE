"""
Brain Control Panel — API de administración del Cognitive Memory Engine.

Router FastAPI con prefix /brain, tag brain-control-panel.
Todos los endpoints requieren autenticación de administrador (require_admin).
Todos los queries filtran por tenant_id del usuario autenticado.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.database import get_db
from app.models.area import Area
from app.models.cme import (
    AreaEpisode,
    AreaPattern,
    AreaMethodology,
    AreaContradiction,
    AreaKnowledgeGap,
    AreaConceptEdge,
    SynthesisReport,
    ProactiveAlert,
    RLHFDataset,
    ConsolidationLog,
    AgentDrive,
    AgentIdentity,
    CrossDomainInsight,
    CuriosityQueue,
    UserCognitiveProfile,
)
from app.routes.auth import require_admin
from app.models.user import User

router = APIRouter(prefix="/brain", tags=["brain-control-panel"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_area(area_id: str, tenant_id: str, db: AsyncSession) -> Area:
    """Obtiene el área verificando que pertenece al tenant del usuario."""
    result = await db.execute(
        select(Area).where(Area.id == area_id, Area.tenant_id == tenant_id)
    )
    area = result.scalar_one_or_none()
    if not area:
        raise HTTPException(status_code=404, detail="Área no encontrada")
    return area


# ─────────────────────────────────────────────────────────────────────────────
# TAREA 21 — ENDPOINTS DE LECTURA
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{area_id}/episodes")
async def list_episodes(
    area_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    arc: Optional[str] = Query(None),
    quality_min: Optional[float] = Query(None, ge=0.0, le=1.0),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lista paginada de episodios del área."""
    await _get_area(area_id, user.tenant_id, db)

    query = select(AreaEpisode).where(
        AreaEpisode.area_id == area_id,
        AreaEpisode.tenant_id == user.tenant_id,
    )
    if arc:
        query = query.where(AreaEpisode.session_arc == arc)
    if quality_min is not None:
        query = query.where(AreaEpisode.quality_score >= quality_min)

    query = query.order_by(AreaEpisode.created_at.desc())
    offset = (page - 1) * limit
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    episodes = result.scalars().all()

    # Count total
    count_q = select(func.count(AreaEpisode.id)).where(
        AreaEpisode.area_id == area_id,
        AreaEpisode.tenant_id == user.tenant_id,
    )
    if arc:
        count_q = count_q.where(AreaEpisode.session_arc == arc)
    if quality_min is not None:
        count_q = count_q.where(AreaEpisode.quality_score >= quality_min)
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [
            {
                "id": ep.id,
                "situation": ep.situation,
                "session_arc": ep.session_arc,
                "quality_score": ep.quality_score,
                "temporal_weight": ep.temporal_weight,
                "emotional_intensity": ep.emotional_intensity,
                "created_at": ep.created_at,
            }
            for ep in episodes
        ],
    }


@router.get("/{area_id}/patterns")
async def list_patterns(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Todos los patrones del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaPattern).where(
            AreaPattern.area_id == area_id,
            AreaPattern.tenant_id == user.tenant_id,
        ).order_by(AreaPattern.confidence_score.desc())
    )
    patterns = result.scalars().all()

    return [
        {
            "id": p.id,
            "trigger_description": p.trigger_description,
            "response_description": p.response_description,
            "causal_mechanism": p.causal_mechanism,
            "confidence_score": p.confidence_score,
            "diversity_score": p.diversity_score,
            "distinct_user_count": p.distinct_user_count,
            "abstraction_level": p.abstraction_level,
            "is_approved": p.is_approved,
            "episode_count": p.episode_count,
            "is_failure_pattern": p.is_failure_pattern,
            "created_at": p.created_at,
        }
        for p in patterns
    ]


@router.get("/{area_id}/methodologies")
async def list_methodologies(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Todas las metodologías del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaMethodology).where(
            AreaMethodology.area_id == area_id,
            AreaMethodology.tenant_id == user.tenant_id,
        ).order_by(AreaMethodology.created_at.desc())
    )
    methodologies = result.scalars().all()

    return [
        {
            "id": m.id,
            "title": m.title,
            "description": m.description,
            "is_approved": m.is_approved,
            "created_at": m.created_at,
        }
        for m in methodologies
    ]


@router.get("/{area_id}/contradictions")
async def list_contradictions(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Contradicciones pendientes del área con descripciones de ambos patrones."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaContradiction).where(
            AreaContradiction.area_id == area_id,
            AreaContradiction.status == "pending",
        ).order_by(AreaContradiction.created_at.desc())
    )
    contradictions = result.scalars().all()

    items = []
    for c in contradictions:
        # Obtener descripciones de ambos patrones
        pa_result = await db.execute(
            select(AreaPattern).where(AreaPattern.id == c.pattern_a_id)
        )
        pa = pa_result.scalar_one_or_none()

        pb_result = await db.execute(
            select(AreaPattern).where(AreaPattern.id == c.pattern_b_id)
        )
        pb = pb_result.scalar_one_or_none()

        items.append({
            "id": c.id,
            "description": c.description,
            "status": c.status,
            "created_at": c.created_at,
            "pattern_a": {
                "id": c.pattern_a_id,
                "trigger_description": pa.trigger_description if pa else None,
                "response_description": pa.response_description if pa else None,
            },
            "pattern_b": {
                "id": c.pattern_b_id,
                "trigger_description": pb.trigger_description if pb else None,
                "response_description": pb.response_description if pb else None,
            },
        })

    return items


@router.get("/{area_id}/knowledge-gaps")
async def list_knowledge_gaps(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Knowledge gaps del área ordenados por occurrence_count desc."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaKnowledgeGap).where(
            AreaKnowledgeGap.area_id == area_id,
            AreaKnowledgeGap.tenant_id == user.tenant_id,
        ).order_by(AreaKnowledgeGap.occurrence_count.desc())
    )
    gaps = result.scalars().all()

    return [
        {
            "id": g.id,
            "topic_description": g.topic_description,
            "occurrence_count": g.occurrence_count,
            "status": g.status,
            "first_seen_at": g.first_seen_at,
            "last_seen_at": g.last_seen_at,
        }
        for g in gaps
    ]


@router.get("/{area_id}/concept-graph")
async def get_concept_graph(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Top 20 aristas del concept graph por peso."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaConceptEdge).where(
            AreaConceptEdge.area_id == area_id,
        ).order_by(AreaConceptEdge.weight.desc()).limit(20)
    )
    edges = result.scalars().all()

    return [
        {
            "id": e.id,
            "concept_a": e.concept_a,
            "concept_b": e.concept_b,
            "weight": e.weight,
            "last_reinforced_at": e.last_reinforced_at,
        }
        for e in edges
    ]


@router.get("/{area_id}/reports")
async def list_reports(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Últimos 4 synthesis reports del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(SynthesisReport).where(
            SynthesisReport.area_id == area_id,
            SynthesisReport.tenant_id == user.tenant_id,
        ).order_by(SynthesisReport.created_at.desc()).limit(4)
    )
    reports = result.scalars().all()

    return [
        {
            "id": r.id,
            "report_date": r.report_date,
            "summary_text": r.summary_text,
            "content": r.content,
            "created_at": r.created_at,
        }
        for r in reports
    ]


@router.get("/{area_id}/alerts")
async def list_alerts(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Alertas proactivas activas del área."""
    await _get_area(area_id, user.tenant_id, db)

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ProactiveAlert).where(
            ProactiveAlert.area_id == area_id,
            ProactiveAlert.tenant_id == user.tenant_id,
            ProactiveAlert.status == "active",
        ).order_by(ProactiveAlert.created_at.desc())
    )
    alerts = result.scalars().all()

    return [
        {
            "id": a.id,
            "alert_message": a.alert_message,
            "trigger_count": a.trigger_count,
            "suggested_action": a.suggested_action,
            "status": a.status,
            "pattern_id": a.pattern_id,
            "dismissed_until": a.dismissed_until,
            "created_at": a.created_at,
        }
        for a in alerts
    ]


@router.get("/{area_id}/rlhf-stats")
async def get_rlhf_stats(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Estadísticas del dataset RLHF del área."""
    await _get_area(area_id, user.tenant_id, db)

    count_result = await db.execute(
        select(func.count(RLHFDataset.id)).where(
            RLHFDataset.area_id == area_id,
            RLHFDataset.tenant_id == user.tenant_id,
        )
    )
    count = count_result.scalar() or 0

    avg_result = await db.execute(
        select(func.avg(RLHFDataset.quality_score)).where(
            RLHFDataset.area_id == area_id,
            RLHFDataset.tenant_id == user.tenant_id,
        )
    )
    avg_quality = avg_result.scalar()

    return {
        "area_id": area_id,
        "total_records": count,
        "avg_quality_score": round(avg_quality, 4) if avg_quality else None,
    }


@router.get("/{area_id}/consolidation-log")
async def get_consolidation_log(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Último log de consolidación del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(ConsolidationLog).where(
            ConsolidationLog.tenant_id == user.tenant_id,
        ).order_by(ConsolidationLog.created_at.desc()).limit(1)
    )
    log = result.scalar_one_or_none()

    if not log:
        return {"message": "No hay logs de consolidación disponibles"}

    return {
        "id": log.id,
        "tenant_id": log.tenant_id,
        "area_id": log.area_id,
        "patterns_merged": log.patterns_merged,
        "edges_pruned": log.edges_pruned,
        "episodes_reweighted": log.episodes_reweighted,
        "duration_seconds": log.duration_seconds,
        "created_at": log.created_at,
    }


@router.get("/{area_id}/drives")
async def list_drives(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Drives del agente del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AgentDrive).where(
            AgentDrive.area_id == area_id,
            AgentDrive.tenant_id == user.tenant_id,
        )
    )
    drives = result.scalars().all()

    return [
        {
            "id": d.id,
            "drive_type": d.drive_type,
            "current_value": d.current_value,
            "target_value": d.target_value,
            "tension": d.tension,
            "is_enabled": d.is_enabled,
            "updated_at": d.updated_at,
        }
        for d in drives
    ]


@router.get("/{area_id}/identity")
async def get_identity(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Identidad del agente del área si existe."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AgentIdentity).where(
            AgentIdentity.area_id == area_id,
            AgentIdentity.tenant_id == user.tenant_id,
        )
    )
    identity = result.scalar_one_or_none()

    if not identity:
        return {"message": "No hay identidad configurada para esta área"}

    return {
        "id": identity.id,
        "name": identity.name,
        "birth_date": identity.birth_date,
        "total_sessions": identity.total_sessions,
        "total_episodes": identity.total_episodes,
        "self_description": identity.self_description,
        "core_values": json.loads(identity.core_values or "[]"),
        "is_enabled": identity.is_enabled,
        "last_updated_at": identity.last_updated_at,
    }


@router.get("/{area_id}/cross-domain-insights")
async def list_cross_domain_insights(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Conexiones cross-domain del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(CrossDomainInsight).where(
            CrossDomainInsight.area_id == area_id,
        ).order_by(CrossDomainInsight.confidence.desc())
    )
    insights = result.scalars().all()

    return [
        {
            "id": i.id,
            "episode_a_id": i.episode_a_id,
            "episode_b_id": i.episode_b_id,
            "connection_description": i.connection_description,
            "confidence": i.confidence,
            "status": i.status,
            "created_at": i.created_at,
        }
        for i in insights
    ]


@router.get("/{area_id}/curiosity-queue")
async def list_curiosity_queue(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Cola de preguntas de curiosidad pendientes del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(CuriosityQueue).where(
            CuriosityQueue.area_id == area_id,
            CuriosityQueue.status == "pending",
        ).order_by(CuriosityQueue.created_at.desc())
    )
    items = result.scalars().all()

    return [
        {
            "id": q.id,
            "gap_id": q.gap_id,
            "question_text": q.question_text,
            "status": q.status,
            "created_at": q.created_at,
        }
        for q in items
    ]


@router.get("/{area_id}/cognitive-profiles/stats")
async def get_cognitive_profiles_stats(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Estadísticas agregadas de perfiles cognitivos del área (sin exponer perfiles individuales)."""
    await _get_area(area_id, user.tenant_id, db)

    # Promedio de expertise_level
    avg_result = await db.execute(
        select(func.avg(UserCognitiveProfile.expertise_level)).where(
            UserCognitiveProfile.area_id == area_id,
            UserCognitiveProfile.tenant_id == user.tenant_id,
        )
    )
    avg_expertise = avg_result.scalar()

    # Total de perfiles
    count_result = await db.execute(
        select(func.count(UserCognitiveProfile.id)).where(
            UserCognitiveProfile.area_id == area_id,
            UserCognitiveProfile.tenant_id == user.tenant_id,
        )
    )
    total_profiles = count_result.scalar() or 0

    # Distribución de preferred_detail_level
    detail_levels = ["brief", "standard", "detailed"]
    distribution = {}
    for level in detail_levels:
        level_result = await db.execute(
            select(func.count(UserCognitiveProfile.id)).where(
                UserCognitiveProfile.area_id == area_id,
                UserCognitiveProfile.tenant_id == user.tenant_id,
                UserCognitiveProfile.preferred_detail_level == level,
            )
        )
        distribution[level] = level_result.scalar() or 0

    return {
        "area_id": area_id,
        "total_profiles": total_profiles,
        "avg_expertise_level": round(avg_expertise, 4) if avg_expertise else None,
        "preferred_detail_level_distribution": distribution,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TAREA 22 — ENDPOINTS DE ACCIÓN
# ─────────────────────────────────────────────────────────────────────────────

# ── Pydantic bodies ──────────────────────────────────────────────────────────

class ApprovePatternBody(BaseModel):
    is_approved: bool


class UpdateMethodologyBody(BaseModel):
    is_approved: Optional[bool] = None
    description: Optional[str] = None


class ResolveContradictionBody(BaseModel):
    authoritative_pattern_id: str


class UpdateLambdaBody(BaseModel):
    lambda_rate: float


class ResetLearningBody(BaseModel):
    confirm: bool


class DismissAlertBody(BaseModel):
    pass


class UpdateDriveBody(BaseModel):
    target_value: Optional[float] = None
    is_enabled: Optional[bool] = None
    tension: Optional[float] = None


class UpdateIdentityBody(BaseModel):
    self_description: Optional[str] = None
    core_values: Optional[list] = None
    name: Optional[str] = None


class ActivateIdentityBody(BaseModel):
    is_enabled: bool


# ── Endpoints de acción ──────────────────────────────────────────────────────

@router.patch("/{area_id}/patterns/{pattern_id}/approve")
async def approve_pattern(
    area_id: str,
    pattern_id: str,
    body: ApprovePatternBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Aprobar o rechazar un patrón del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaPattern).where(
            AreaPattern.id == pattern_id,
            AreaPattern.area_id == area_id,
            AreaPattern.tenant_id == user.tenant_id,
        )
    )
    pattern = result.scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Patrón no encontrado")

    pattern.is_approved = body.is_approved
    await db.commit()
    await db.refresh(pattern)

    return {
        "id": pattern.id,
        "is_approved": pattern.is_approved,
        "message": "Patrón actualizado correctamente",
    }


@router.patch("/{area_id}/methodologies/{methodology_id}")
async def update_methodology(
    area_id: str,
    methodology_id: str,
    body: UpdateMethodologyBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Aprobar, rechazar o editar una metodología del área."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaMethodology).where(
            AreaMethodology.id == methodology_id,
            AreaMethodology.area_id == area_id,
            AreaMethodology.tenant_id == user.tenant_id,
        )
    )
    methodology = result.scalar_one_or_none()
    if not methodology:
        raise HTTPException(status_code=404, detail="Metodología no encontrada")

    if body.is_approved is not None:
        methodology.is_approved = body.is_approved
    if body.description is not None:
        methodology.description = body.description

    await db.commit()
    await db.refresh(methodology)

    return {
        "id": methodology.id,
        "title": methodology.title,
        "description": methodology.description,
        "is_approved": methodology.is_approved,
        "message": "Metodología actualizada correctamente",
    }


@router.post("/{area_id}/contradictions/{contradiction_id}/resolve")
async def resolve_contradiction(
    area_id: str,
    contradiction_id: str,
    body: ResolveContradictionBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Resuelve una contradicción seleccionando el patrón autoritativo.
    Desactiva el patrón rechazado (is_approved=False) y restaura el autoritativo.
    """
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaContradiction).where(
            AreaContradiction.id == contradiction_id,
            AreaContradiction.area_id == area_id,
        )
    )
    contradiction = result.scalar_one_or_none()
    if not contradiction:
        raise HTTPException(status_code=404, detail="Contradicción no encontrada")

    if contradiction.status == "resolved":
        raise HTTPException(status_code=400, detail="La contradicción ya está resuelta")

    # Verificar que el patrón autoritativo es uno de los dos en conflicto
    authoritative_id = body.authoritative_pattern_id
    if authoritative_id not in (contradiction.pattern_a_id, contradiction.pattern_b_id):
        raise HTTPException(
            status_code=400,
            detail="El patrón autoritativo debe ser uno de los dos patrones en conflicto",
        )

    # Determinar el patrón rechazado
    rejected_id = (
        contradiction.pattern_b_id
        if authoritative_id == contradiction.pattern_a_id
        else contradiction.pattern_a_id
    )

    # Desactivar el patrón rechazado
    rejected_result = await db.execute(
        select(AreaPattern).where(
            AreaPattern.id == rejected_id,
            AreaPattern.tenant_id == user.tenant_id,
        )
    )
    rejected_pattern = rejected_result.scalar_one_or_none()
    if rejected_pattern:
        rejected_pattern.is_approved = False

    # Restaurar el patrón autoritativo
    auth_result = await db.execute(
        select(AreaPattern).where(
            AreaPattern.id == authoritative_id,
            AreaPattern.tenant_id == user.tenant_id,
        )
    )
    auth_pattern = auth_result.scalar_one_or_none()
    if auth_pattern:
        auth_pattern.is_approved = True

    # Marcar contradicción como resuelta
    contradiction.status = "resolved"

    await db.commit()

    return {
        "id": contradiction.id,
        "status": "resolved",
        "authoritative_pattern_id": authoritative_id,
        "rejected_pattern_id": rejected_id,
        "message": "Contradicción resuelta correctamente",
    }


@router.patch("/{area_id}/knowledge-gaps/{gap_id}/address")
async def address_knowledge_gap(
    area_id: str,
    gap_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Marca un knowledge gap como addressed."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AreaKnowledgeGap).where(
            AreaKnowledgeGap.id == gap_id,
            AreaKnowledgeGap.area_id == area_id,
            AreaKnowledgeGap.tenant_id == user.tenant_id,
        )
    )
    gap = result.scalar_one_or_none()
    if not gap:
        raise HTTPException(status_code=404, detail="Knowledge gap no encontrado")

    gap.status = "addressed"
    await db.commit()

    return {
        "id": gap.id,
        "status": "addressed",
        "message": "Knowledge gap marcado como addressed",
    }


@router.patch("/{area_id}/lambda")
async def update_lambda(
    area_id: str,
    body: UpdateLambdaBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Actualiza la tasa de olvido (lambda_rate) del área y dispara
    recálculo manual de temporal_weight via ForgettingCurve.
    """
    area = await _get_area(area_id, user.tenant_id, db)

    if body.lambda_rate <= 0:
        raise HTTPException(status_code=400, detail="lambda_rate debe ser mayor que 0")

    area.cme_lambda_rate = body.lambda_rate
    await db.commit()

    # Disparar recálculo manual de temporal_weight
    try:
        from app.cme.forgetting_curve import forgetting_curve
        updated_count = await forgetting_curve.apply_decay_for_area(
            area_id=area_id,
            lambda_rate=body.lambda_rate,
            db=db,
        )
    except Exception as e:
        updated_count = 0

    return {
        "area_id": area_id,
        "lambda_rate": body.lambda_rate,
        "episodes_reweighted": updated_count,
        "message": "Lambda actualizado y temporal_weight recalculado",
    }


@router.post("/{area_id}/reset")
async def reset_learning(
    area_id: str,
    body: ResetLearningBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Reset Learning: elimina episodios, patrones, metodologías, concept edges,
    gaps y contradicciones del área. Preserva configuración del área y documentos RAG.
    Requiere confirmación explícita en el body.
    """
    await _get_area(area_id, user.tenant_id, db)

    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Se requiere confirm=true para ejecutar el reset",
        )

    # Eliminar en orden para respetar FKs
    await db.execute(
        delete(AreaContradiction).where(AreaContradiction.area_id == area_id)
    )
    await db.execute(
        delete(AreaKnowledgeGap).where(
            AreaKnowledgeGap.area_id == area_id,
            AreaKnowledgeGap.tenant_id == user.tenant_id,
        )
    )
    await db.execute(
        delete(AreaConceptEdge).where(AreaConceptEdge.area_id == area_id)
    )
    await db.execute(
        delete(AreaMethodology).where(
            AreaMethodology.area_id == area_id,
            AreaMethodology.tenant_id == user.tenant_id,
        )
    )
    await db.execute(
        delete(AreaPattern).where(
            AreaPattern.area_id == area_id,
            AreaPattern.tenant_id == user.tenant_id,
        )
    )
    await db.execute(
        delete(AreaEpisode).where(
            AreaEpisode.area_id == area_id,
            AreaEpisode.tenant_id == user.tenant_id,
        )
    )

    await db.commit()

    return {
        "area_id": area_id,
        "message": "Reset completado. Episodios, patrones, metodologías, concept edges, gaps y contradicciones eliminados.",
    }


@router.post("/{area_id}/consolidate")
async def trigger_consolidation(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Dispara consolidación nocturna manual para el tenant del área."""
    area = await _get_area(area_id, user.tenant_id, db)

    try:
        from app.cme.nocturnal_consolidation import nocturnal_consolidation
        log = await nocturnal_consolidation.run_for_tenant(
            tenant_id=user.tenant_id,
            db=db,
        )
        return {
            "message": "Consolidación completada",
            "log_id": log.id,
            "patterns_merged": log.patterns_merged,
            "edges_pruned": log.edges_pruned,
            "episodes_reweighted": log.episodes_reweighted,
            "duration_seconds": log.duration_seconds,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error durante la consolidación: {str(e)}",
        )


@router.get("/{area_id}/rlhf-export")
async def export_rlhf(
    area_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Exporta el dataset RLHF del área como JSONL (application/x-ndjson)."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(RLHFDataset).where(
            RLHFDataset.area_id == area_id,
            RLHFDataset.tenant_id == user.tenant_id,
        ).order_by(RLHFDataset.created_at.asc())
    )
    records = result.scalars().all()

    lines = []
    for r in records:
        try:
            message_pairs = json.loads(r.message_pairs)
        except Exception:
            message_pairs = []

        line = json.dumps({
            "id": r.id,
            "quality_score": r.quality_score,
            "session_arc": r.session_arc,
            "message_pairs": message_pairs,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }, ensure_ascii=False)
        lines.append(line)

    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f"attachment; filename=rlhf_export_{area_id}.jsonl"
        },
    )


@router.post("/{area_id}/alerts/{alert_id}/dismiss")
async def dismiss_alert(
    area_id: str,
    alert_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Descarta una alerta proactiva por 7 días."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(ProactiveAlert).where(
            ProactiveAlert.id == alert_id,
            ProactiveAlert.area_id == area_id,
            ProactiveAlert.tenant_id == user.tenant_id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")

    alert.status = "dismissed"
    alert.dismissed_until = datetime.now(timezone.utc) + timedelta(days=7)
    await db.commit()

    return {
        "id": alert.id,
        "status": "dismissed",
        "dismissed_until": alert.dismissed_until,
        "message": "Alerta descartada por 7 días",
    }


@router.patch("/{area_id}/drives/{drive_id}")
async def update_drive(
    area_id: str,
    drive_id: str,
    body: UpdateDriveBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza target_value, is_enabled o tension de un drive del agente."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AgentDrive).where(
            AgentDrive.id == drive_id,
            AgentDrive.area_id == area_id,
            AgentDrive.tenant_id == user.tenant_id,
        )
    )
    drive = result.scalar_one_or_none()
    if not drive:
        raise HTTPException(status_code=404, detail="Drive no encontrado")

    if body.target_value is not None:
        drive.target_value = body.target_value
    if body.is_enabled is not None:
        drive.is_enabled = body.is_enabled
    if body.tension is not None:
        drive.tension = body.tension

    await db.commit()
    await db.refresh(drive)

    return {
        "id": drive.id,
        "drive_type": drive.drive_type,
        "target_value": drive.target_value,
        "is_enabled": drive.is_enabled,
        "tension": drive.tension,
        "message": "Drive actualizado correctamente",
    }


@router.patch("/{area_id}/identity")
async def update_identity(
    area_id: str,
    body: UpdateIdentityBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Edita self_description, core_values o name de la identidad del agente."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AgentIdentity).where(
            AgentIdentity.area_id == area_id,
            AgentIdentity.tenant_id == user.tenant_id,
        )
    )
    identity = result.scalar_one_or_none()
    if not identity:
        raise HTTPException(status_code=404, detail="Identidad no encontrada para esta área")

    if body.self_description is not None:
        identity.self_description = body.self_description
    if body.core_values is not None:
        identity.core_values = json.dumps(body.core_values)
    if body.name is not None:
        identity.name = body.name

    await db.commit()
    await db.refresh(identity)

    return {
        "id": identity.id,
        "name": identity.name,
        "self_description": identity.self_description,
        "core_values": json.loads(identity.core_values or "[]"),
        "is_enabled": identity.is_enabled,
        "message": "Identidad actualizada correctamente",
    }


@router.post("/{area_id}/identity/activate")
async def activate_identity(
    area_id: str,
    body: ActivateIdentityBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Activa o desactiva el sistema de identidad del agente."""
    await _get_area(area_id, user.tenant_id, db)

    result = await db.execute(
        select(AgentIdentity).where(
            AgentIdentity.area_id == area_id,
            AgentIdentity.tenant_id == user.tenant_id,
        )
    )
    identity = result.scalar_one_or_none()

    if not identity:
        # Crear identidad si no existe (opt-in)
        identity = AgentIdentity(
            area_id=area_id,
            tenant_id=user.tenant_id,
            name="IM",
            birth_date=datetime.now(timezone.utc),
            is_enabled=body.is_enabled,
        )
        db.add(identity)
    else:
        identity.is_enabled = body.is_enabled

    await db.commit()
    await db.refresh(identity)

    action = "activado" if body.is_enabled else "desactivado"
    return {
        "id": identity.id,
        "is_enabled": identity.is_enabled,
        "message": f"Sistema de identidad {action} correctamente",
    }


# ─────────────────────────────────────────────────────────────────────────────
# CORE BRAIN — Lectura del núcleo ciego (solo admin/investigador)
# El CoreBrain agrega sin retroalimentar. Ningún usuario normal accede aquí.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/core")
async def get_core_brain(
    status: Optional[str] = Query(None, description="Filtrar por status: pending_emergence|emerged|dismissed"),
    include_dismissed: bool = Query(False),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna las entradas del CoreBrain para el tenant del admin.

    El CoreBrain es el núcleo ciego — agrega patrones de todas las instancias
    de usuario sin retroalimentar a ninguna. Solo el investigador/admin puede
    leer este espacio.

    Los patrones con status='emerged' son los más interesantes: aparecieron
    en múltiples instancias independientes sin que ningún usuario lo supiera.
    """
    from app.config import settings
    if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
        raise HTTPException(
            status_code=400,
            detail="El modo experimental CME_EXPERIMENTAL_USER_ISOLATION no está activo."
        )

    from app.models.cme import CoreBrainEntry
    from app.cme.core_brain import core_brain

    if status:
        q = await db.execute(
            select(CoreBrainEntry)
            .where(
                CoreBrainEntry.tenant_id == user.tenant_id,
                CoreBrainEntry.status == status,
            )
            .order_by(CoreBrainEntry.emergence_score.desc())
        )
    else:
        entries = await core_brain.get_all_entries(
            user.tenant_id, db, include_dismissed=include_dismissed
        )
        return [
            {
                "id": e.id,
                "trigger_description": e.trigger_description,
                "response_description": e.response_description,
                "confidence_score": e.confidence_score,
                "contributor_count": e.contributor_count,
                "episode_count": e.episode_count,
                "emergence_score": e.emergence_score,
                "status": e.status,
                "temporal_signal": e.temporal_signal,
                "created_at": str(e.created_at),
                "updated_at": str(e.updated_at),
            }
            for e in entries
        ]

    entries = q.scalars().all()
    return [
        {
            "id": e.id,
            "trigger_description": e.trigger_description,
            "response_description": e.response_description,
            "confidence_score": e.confidence_score,
            "contributor_count": e.contributor_count,
            "episode_count": e.episode_count,
            "emergence_score": e.emergence_score,
            "status": e.status,
            "temporal_signal": e.temporal_signal,
            "created_at": str(e.created_at),
            "updated_at": str(e.updated_at),
        }
        for e in entries
    ]


@router.get("/core/emerged")
async def get_emerged_patterns(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna solo los patrones emergentes del CoreBrain.

    Un patrón es 'emergente' cuando aparece en múltiples instancias de usuario
    independientes (emergence_score >= CME_CORE_EMERGENCE_THRESHOLD).
    Estos son los patrones que ningún usuario generó solo — emergieron del colectivo.
    """
    from app.config import settings
    if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
        raise HTTPException(status_code=400, detail="Modo experimental no activo.")

    from app.cme.core_brain import core_brain
    entries = await core_brain.get_emerged_patterns(user.tenant_id, db)

    return {
        "emergence_threshold": settings.CME_CORE_EMERGENCE_THRESHOLD,
        "total_emerged": len(entries),
        "patterns": [
            {
                "id": e.id,
                "trigger_description": e.trigger_description,
                "response_description": e.response_description,
                "confidence_score": e.confidence_score,
                "contributor_count": e.contributor_count,
                "episode_count": e.episode_count,
                "emergence_score": e.emergence_score,
                "temporal_signal": e.temporal_signal,
                "created_at": str(e.created_at),
            }
            for e in entries
        ],
    }


@router.delete("/core/{entry_id}")
async def dismiss_core_entry(
    entry_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Descarta una entrada del CoreBrain."""
    from app.config import settings
    if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
        raise HTTPException(status_code=400, detail="Modo experimental no activo.")

    from app.cme.core_brain import core_brain
    success = await core_brain.dismiss(entry_id, db)
    if not success:
        raise HTTPException(status_code=404, detail="Entrada no encontrada.")
    return {"ok": True}


@router.get("/user-brain/stats")
async def get_user_brain_stats(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna estadísticas agregadas de todos los UserBrains del tenant.
    Muestra cuántos episodios y patrones tiene cada instancia — sin revelar contenido.
    """
    from app.config import settings
    if not settings.CME_EXPERIMENTAL_USER_ISOLATION:
        raise HTTPException(status_code=400, detail="Modo experimental no activo.")

    from app.models.cme import UserEpisode, UserPattern
    from app.models.user import User as UserModel

    # Obtener usuarios del tenant
    users_q = await db.execute(
        select(UserModel)
        .where(UserModel.tenant_id == user.tenant_id, UserModel.is_active == True)
    )
    users = users_q.scalars().all()

    stats = []
    for u in users:
        ep_q = await db.execute(
            select(func.count(UserEpisode.id))
            .where(UserEpisode.user_id == u.id)
        )
        pat_q = await db.execute(
            select(func.count(UserPattern.id))
            .where(UserPattern.user_id == u.id)
        )
        stats.append({
            # Anonimizar: mostrar solo el índice, no el nombre
            "instance": f"instancia_{users.index(u) + 1}",
            "total_episodes": ep_q.scalar() or 0,
            "total_patterns": pat_q.scalar() or 0,
        })

    return {
        "total_instances": len(stats),
        "instances": stats,
    }
