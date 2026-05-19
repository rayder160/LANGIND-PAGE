"""
Synthesis Reporter — Genera reportes semanales del estado del Area Brain.

Recopila métricas de la semana y genera un resumen en español via LLM.
Guarda SynthesisReport en DB con content (JSON) y summary_text.
"""
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.cme import (
    AreaEpisode,
    AreaPattern,
    AreaMethodology,
    AreaKnowledgeGap,
    AreaContradiction,
    AreaConceptEdge,
    SynthesisReport,
)
from app.config import settings

logger = logging.getLogger(__name__)

REPORT_LOOKBACK_DAYS = 7


class SynthesisReporter:

    async def generate_report(
        self,
        area_id: str,
        tenant_id: str,
        db: AsyncSession
    ) -> SynthesisReport:
        """
        Recopila métricas de la semana y genera un reporte con summary_text en español.
        Si no hubo episodios nuevos: genera reporte indicando cero actividad + knowledge gap count.
        Guarda SynthesisReport en DB.
        """
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=REPORT_LOOKBACK_DAYS)
        report_date = now.strftime("%Y-%m-%d")

        try:
            # 1. Nuevos episodios en la semana
            new_episodes_q = await db.execute(
                select(func.count(AreaEpisode.id))
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.created_at >= week_start,
                    AreaEpisode.extraction_status == "completed",
                )
            )
            new_episodes = new_episodes_q.scalar() or 0

            # 2. Nuevos patrones en la semana
            new_patterns_q = await db.execute(
                select(func.count(AreaPattern.id))
                .where(
                    AreaPattern.area_id == area_id,
                    AreaPattern.created_at >= week_start,
                )
            )
            new_patterns = new_patterns_q.scalar() or 0

            # 3. Metodologías promovidas (aprobadas) en la semana
            methodologies_promoted_q = await db.execute(
                select(func.count(AreaMethodology.id))
                .where(
                    AreaMethodology.area_id == area_id,
                    AreaMethodology.is_approved == True,
                    AreaMethodology.created_at >= week_start,
                )
            )
            methodologies_promoted = methodologies_promoted_q.scalar() or 0

            # 4. Top 5 knowledge gaps por occurrence_count
            top_gaps_q = await db.execute(
                select(AreaKnowledgeGap)
                .where(
                    AreaKnowledgeGap.area_id == area_id,
                    AreaKnowledgeGap.status == "pending",
                )
                .order_by(AreaKnowledgeGap.occurrence_count.desc())
                .limit(5)
            )
            top_gaps = top_gaps_q.scalars().all()
            top_5_knowledge_gaps = [
                {
                    "topic": gap.topic_description[:200],
                    "occurrences": gap.occurrence_count,
                }
                for gap in top_gaps
            ]

            # Total de gaps pendientes (para reporte de cero actividad)
            total_gaps_q = await db.execute(
                select(func.count(AreaKnowledgeGap.id))
                .where(
                    AreaKnowledgeGap.area_id == area_id,
                    AreaKnowledgeGap.status == "pending",
                )
            )
            total_gaps = total_gaps_q.scalar() or 0

            # 5. Contradicciones activas
            active_contradictions_q = await db.execute(
                select(func.count(AreaContradiction.id))
                .where(
                    AreaContradiction.area_id == area_id,
                    AreaContradiction.status == "pending",
                )
            )
            active_contradictions = active_contradictions_q.scalar() or 0

            # 6. Nuevas aristas en el concept graph esta semana
            concept_graph_new_edges_q = await db.execute(
                select(func.count(AreaConceptEdge.id))
                .where(
                    AreaConceptEdge.area_id == area_id,
                    AreaConceptEdge.last_reinforced_at >= week_start,
                )
            )
            concept_graph_new_edges = concept_graph_new_edges_q.scalar() or 0

            # 7. Calidad promedio de episodios de la semana
            avg_quality_q = await db.execute(
                select(func.avg(AreaEpisode.quality_score))
                .where(
                    AreaEpisode.area_id == area_id,
                    AreaEpisode.created_at >= week_start,
                    AreaEpisode.quality_score.isnot(None),
                )
            )
            avg_quality_score = avg_quality_q.scalar()
            avg_quality_score = round(float(avg_quality_score), 4) if avg_quality_score else None

            # Construir content JSON
            content_data = {
                "period": {
                    "start": week_start.isoformat(),
                    "end": now.isoformat(),
                },
                "new_episodes": new_episodes,
                "new_patterns": new_patterns,
                "methodologies_promoted": methodologies_promoted,
                "top_5_knowledge_gaps": top_5_knowledge_gaps,
                "active_contradictions": active_contradictions,
                "concept_graph_new_edges": concept_graph_new_edges,
                "avg_quality_score": avg_quality_score,
                "total_pending_gaps": total_gaps,
            }

            # Generar summary_text via LLM
            if new_episodes == 0:
                summary_text = await self._generate_zero_activity_summary(
                    total_gaps, active_contradictions
                )
            else:
                summary_text = await self._generate_summary(content_data)

            # Guardar reporte en DB
            report = SynthesisReport(
                area_id=area_id,
                tenant_id=tenant_id,
                report_date=report_date,
                content=json.dumps(content_data, ensure_ascii=False),
                summary_text=summary_text,
            )
            db.add(report)
            await db.commit()
            await db.refresh(report)

            logger.info(
                f"CME SynthesisReporter: reporte generado para área {area_id} "
                f"({new_episodes} episodios, {new_patterns} patrones)"
            )
            return report

        except Exception as e:
            logger.error(f"CME SynthesisReporter: error en generate_report: {e}")
            # Crear reporte de error mínimo para no romper el flujo
            error_report = SynthesisReport(
                area_id=area_id,
                tenant_id=tenant_id,
                report_date=report_date,
                content=json.dumps({"error": str(e)}, ensure_ascii=False),
                summary_text="Error al generar el reporte semanal. Revisar logs del sistema.",
            )
            db.add(error_report)
            await db.commit()
            return error_report

    async def _generate_summary(self, content_data: dict) -> str:
        """Genera summary_text en español via LLM (≤200 palabras)."""
        fallback = self._build_fallback_summary(content_data)

        try:
            gaps_text = ""
            if content_data["top_5_knowledge_gaps"]:
                gaps_list = "\n".join(
                    f"  - {g['topic']} ({g['occurrences']} ocurrencias)"
                    for g in content_data["top_5_knowledge_gaps"]
                )
                gaps_text = f"\nTop brechas de conocimiento:\n{gaps_list}"

            prompt = (
                f"Genera un resumen ejecutivo semanal del Area Brain en español (≤200 palabras).\n\n"
                f"Métricas de la semana:\n"
                f"- Nuevos episodios: {content_data['new_episodes']}\n"
                f"- Nuevos patrones detectados: {content_data['new_patterns']}\n"
                f"- Metodologías promovidas: {content_data['methodologies_promoted']}\n"
                f"- Contradicciones activas: {content_data['active_contradictions']}\n"
                f"- Nuevas conexiones en grafo de conceptos: {content_data['concept_graph_new_edges']}\n"
                f"- Calidad promedio de sesiones: "
                f"{content_data['avg_quality_score']:.0%}" if content_data['avg_quality_score'] else "- Calidad promedio: sin datos"
                f"{gaps_text}\n\n"
                f"El resumen debe ser profesional, orientado a acción, y destacar lo más relevante. "
                f"Solo el texto del resumen, sin títulos ni formato adicional."
            )

            async with httpx.AsyncClient(timeout=30) as client:
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
                    if content and len(content) >= 20:
                        # Truncar a ~200 palabras
                        words = content.split()
                        if len(words) > 200:
                            content = " ".join(words[:200]) + "..."
                        return content

        except Exception as e:
            logger.debug(f"CME SynthesisReporter: LLM falló para summary: {e}")

        return fallback

    async def _generate_zero_activity_summary(
        self,
        total_gaps: int,
        active_contradictions: int
    ) -> str:
        """Genera resumen para semana sin actividad."""
        summary = (
            f"Esta semana no se registraron nuevas sesiones en el área. "
            f"El sistema se mantiene en estado de espera."
        )

        if total_gaps > 0:
            summary += (
                f" Actualmente hay {total_gaps} brecha{'s' if total_gaps != 1 else ''} "
                f"de conocimiento pendiente{'s' if total_gaps != 1 else ''} que podrían "
                f"abordarse con documentación adicional."
            )

        if active_contradictions > 0:
            summary += (
                f" Se detectan {active_contradictions} contradicción{'es' if active_contradictions != 1 else ''} "
                f"activa{'s' if active_contradictions != 1 else ''} entre patrones que requieren revisión."
            )

        return summary

    def _build_fallback_summary(self, content_data: dict) -> str:
        """Construye un resumen determinístico sin LLM."""
        parts = [
            f"Resumen semanal del Area Brain.",
            f"Esta semana se registraron {content_data['new_episodes']} episodio(s) nuevo(s)",
            f"y se detectaron {content_data['new_patterns']} patrón(es) nuevo(s).",
        ]

        if content_data["methodologies_promoted"] > 0:
            parts.append(
                f"Se promovieron {content_data['methodologies_promoted']} metodología(s)."
            )

        if content_data["avg_quality_score"]:
            parts.append(
                f"La calidad promedio de las sesiones fue del "
                f"{content_data['avg_quality_score']:.0%}."
            )

        if content_data["active_contradictions"] > 0:
            parts.append(
                f"Hay {content_data['active_contradictions']} contradicción(es) pendiente(s) "
                f"que requieren atención."
            )

        if content_data["top_5_knowledge_gaps"]:
            top_gap = content_data["top_5_knowledge_gaps"][0]
            parts.append(
                f"La brecha de conocimiento más frecuente es: '{top_gap['topic'][:100]}'."
            )

        return " ".join(parts)


# Instancia global singleton
synthesis_reporter = SynthesisReporter()
