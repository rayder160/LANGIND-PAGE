"""
Cognitive Memory Engine (CME) — Modelos SQLAlchemy
Fases 1, 2 y 3: 19 tablas nuevas para el cerebro cognitivo de Ether-IM
"""
from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, Float, Boolean, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.models.base import Base, gen_id


# ─────────────────────────────────────────────────────────────────────────────
# FASE 1 — CORE COGNITIVO (13 tablas)
# ─────────────────────────────────────────────────────────────────────────────

class AreaEpisode(Base):
    """Episodio estructurado extraído de una sesión de chat completa."""
    __tablename__ = "area_episodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    situation: Mapped[str] = mapped_column(Text)                                          # ≤ 400 chars
    strategy: Mapped[str] = mapped_column(Text)                                           # ≤ 400 chars
    outcome: Mapped[str] = mapped_column(Text)                                            # ≤ 300 chars
    session_arc: Mapped[str] = mapped_column(String)                                      # resolved|degraded|neutral|abandoned
    situation_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)          # JSON float array
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)             # 0-1
    temporal_weight: Mapped[float] = mapped_column(Float, default=1.0)                    # 0-1
    causal_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)           # ≤ 300 chars
    failure_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)             # ≤ 300 chars (sesiones fallidas)
    emotional_intensity: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)  # 0-1
    extraction_status: Mapped[str] = mapped_column(
        String, default="completed"
    )  # completed|extraction_failed|skipped_too_short|skipped_no_content
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AreaPattern(Base):
    """Patrón recurrente detectado en clusters de episodios del área."""
    __tablename__ = "area_patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    trigger_description: Mapped[str] = mapped_column(Text)
    trigger_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)            # JSON float array
    response_description: Mapped[str] = mapped_column(Text)
    response_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)           # JSON float array
    causal_mechanism: Mapped[str | None] = mapped_column(Text, nullable=True)             # ≤ 300 chars
    confidence_score: Mapped[float] = mapped_column(Float, default=0.3)                   # 0-1
    diversity_score: Mapped[float] = mapped_column(Float, default=0.0)                    # 0-1
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_user_count: Mapped[int] = mapped_column(Integer, default=0)
    abstraction_level: Mapped[int] = mapped_column(Integer, default=1)                    # 1=pattern, 2=parent, 3=principle
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_failure_pattern: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_principle_id: Mapped[str | None] = mapped_column(
        ForeignKey("area_patterns.id"), nullable=True
    )
    source_episode_ids: Mapped[str] = mapped_column(Text, default="[]")                   # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AreaMethodology(Base):
    """Metodología aprobada extraída de sesiones de alta calidad."""
    __tablename__ = "area_methodologies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    description_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)        # JSON float array
    source_episode_ids: Mapped[str] = mapped_column(Text, default="[]")                   # JSON array
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AreaConceptEdge(Base):
    """Arista del grafo de conceptos del área (co-ocurrencia de términos)."""
    __tablename__ = "area_concept_edges"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    concept_a: Mapped[str] = mapped_column(String)    # normalizado: lowercase, sin puntuación
    concept_b: Mapped[str] = mapped_column(String)    # normalizado: lowercase, sin puntuación
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    last_reinforced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("area_id", "concept_a", "concept_b", name="uq_concept_edge"),
    )


class AreaKnowledgeGap(Base):
    """Brecha de conocimiento detectada en sesiones abandonadas o degradadas."""
    __tablename__ = "area_knowledge_gaps"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    topic_description: Mapped[str] = mapped_column(Text)
    topic_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)              # JSON float array
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="pending")                        # pending|addressed
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AreaContradiction(Base):
    """Contradicción detectada entre dos patrones del área."""
    __tablename__ = "area_contradictions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    pattern_a_id: Mapped[str] = mapped_column(ForeignKey("area_patterns.id"))
    pattern_b_id: Mapped[str] = mapped_column(ForeignKey("area_patterns.id"))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="pending")                        # pending|resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GlobalPattern(Base):
    """Patrón agregado cross-área a nivel tenant (sin contenido raw de conversaciones)."""
    __tablename__ = "global_patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    trigger_description: Mapped[str] = mapped_column(Text)
    trigger_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)            # JSON float array
    response_description: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    diversity_score: Mapped[float] = mapped_column(Float, default=0.0)
    source_area_ids: Mapped[str] = mapped_column(Text, default="[]")                      # JSON array
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    temporal_relevance_index: Mapped[float] = mapped_column(Float, default=1.0)           # 0-1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GlobalMethodology(Base):
    """Metodología agregada cross-área a nivel tenant."""
    __tablename__ = "global_methodologies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    description_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)        # JSON float array
    source_area_ids: Mapped[str] = mapped_column(Text, default="[]")                      # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RLHFDataset(Base):
    """Dataset de pares de sesión anonimizados para RLHF (quality_score ≥ 0.80)."""
    __tablename__ = "rlhf_dataset"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    quality_score: Mapped[float] = mapped_column(Float)
    session_arc: Mapped[str] = mapped_column(String)
    message_pairs: Mapped[str] = mapped_column(Text)                                      # JSON array de {role, content} anonimizados
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SynthesisReport(Base):
    """Reporte semanal del estado del Area Brain."""
    __tablename__ = "synthesis_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    report_date: Mapped[str] = mapped_column(String)                                      # ISO date YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text)                                            # JSON estructurado
    summary_text: Mapped[str] = mapped_column(Text)                                       # ≤ 200 palabras en español
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProactiveAlert(Base):
    """Alerta proactiva generada para administradores del área."""
    __tablename__ = "proactive_alerts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    pattern_id: Mapped[str | None] = mapped_column(ForeignKey("area_patterns.id"), nullable=True)
    alert_message: Mapped[str] = mapped_column(Text)                                      # ≤ 300 chars
    trigger_count: Mapped[int] = mapped_column(Integer, default=1)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)             # ≤ 300 chars
    status: Mapped[str] = mapped_column(String, default="active")                         # active|dismissed
    dismissed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserCognitiveProfile(Base):
    """Perfil cognitivo del usuario por área (expertise, estilo, temas dominantes)."""
    __tablename__ = "user_cognitive_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    expertise_level: Mapped[float] = mapped_column(Float, default=0.5)                    # 0-1
    preferred_detail_level: Mapped[str] = mapped_column(String, default="standard")       # brief|standard|detailed
    dominant_topics: Mapped[str] = mapped_column(Text, default="[]")                      # JSON array top 5
    avg_message_length: Mapped[float] = mapped_column(Float, default=0.0)
    reformulation_rate: Mapped[float] = mapped_column(Float, default=0.0)
    frustration_frequency: Mapped[float] = mapped_column(Float, default=0.0)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "area_id", name="uq_user_area_profile"),
    )


class ConsolidationLog(Base):
    """Log de ejecución de la consolidación nocturna del Area Brain."""
    __tablename__ = "consolidation_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    area_id: Mapped[str | None] = mapped_column(ForeignKey("areas.id"), nullable=True)
    patterns_merged: Mapped[int] = mapped_column(Integer, default=0)
    edges_pruned: Mapped[int] = mapped_column(Integer, default=0)
    episodes_reweighted: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────────────────────
# FASE 2 — AUTONOMÍA (6 tablas)
# ─────────────────────────────────────────────────────────────────────────────

class AgentDrive(Base):
    """Drive interno del agente: tensión entre estado actual y objetivo."""
    __tablename__ = "agent_drives"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    drive_type: Mapped[str] = mapped_column(String)                                       # gap_reduction|quality_maximization|coherence_maintenance
    current_value: Mapped[float] = mapped_column(Float, default=0.0)
    target_value: Mapped[float] = mapped_column(Float, default=1.0)
    tension: Mapped[float] = mapped_column(Float, default=0.0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("area_id", "drive_type", name="uq_area_drive_type"),
    )


class AgentIdentity(Base):
    """Identidad persistente del agente cognitivo del área."""
    __tablename__ = "agent_identity"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String, default="IM")
    birth_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    total_episodes: Mapped[int] = mapped_column(Integer, default=0)
    self_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    core_values: Mapped[str] = mapped_column(Text, default="[]")                          # JSON array
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("area_id", name="uq_area_identity"),
    )


class CuriosityQueue(Base):
    """Cola de preguntas generadas por el agente sobre brechas de conocimiento."""
    __tablename__ = "curiosity_queue"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    gap_id: Mapped[str] = mapped_column(ForeignKey("area_knowledge_gaps.id"))
    question_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="pending")                        # pending|answered|dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────────────────────
# FASE 3 — COGNICIÓN AVANZADA (3 tablas)
# ─────────────────────────────────────────────────────────────────────────────

class CrossDomainInsight(Base):
    """Conexión inesperada entre episodios de dominios conceptuales distintos."""
    __tablename__ = "cross_domain_insights"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    episode_a_id: Mapped[str] = mapped_column(ForeignKey("area_episodes.id"))
    episode_b_id: Mapped[str] = mapped_column(ForeignKey("area_episodes.id"))
    connection_description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="pending")                        # pending|validated|dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TemporalChain(Base):
    """Cadena causal temporal entre dos episodios del área."""
    __tablename__ = "temporal_chains"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    episode_a_id: Mapped[str] = mapped_column(ForeignKey("area_episodes.id"))
    episode_b_id: Mapped[str] = mapped_column(ForeignKey("area_episodes.id"))
    time_delta_days: Mapped[float] = mapped_column(Float)
    causal_link_description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimulationLog(Base):
    """Log de simulación mental: evaluación interna de payloads candidatos."""
    __tablename__ = "simulation_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    query_embedding: Mapped[str] = mapped_column(Text)                                    # JSON float array
    payload_a_score: Mapped[float] = mapped_column(Float)
    payload_b_score: Mapped[float] = mapped_column(Float)
    selected_payload: Mapped[str] = mapped_column(String)                                 # "a"|"b"
    trigger_reason: Mapped[str] = mapped_column(String)                                   # frustration|failure_pattern|low_quality
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────────────────────
# FASE 3 — UNIVERSAL BRAIN (1 tabla, nivel plataforma, sin tenant_id)
# ─────────────────────────────────────────────────────────────────────────────

class UniversalPattern(Base):
    """
    Patrón universal cross-tenant: conocimiento abstracto que trasciende dominios.
    NUNCA almacena: nombres de tenant/área, identificadores de usuario,
    etiquetas de industria ni terminología específica de empresa. (Req 36.3)
    """
    __tablename__ = "universal_patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    trigger_description: Mapped[str] = mapped_column(Text)                                # descripción abstracta del trigger
    trigger_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)            # JSON float array
    response_description: Mapped[str] = mapped_column(Text)                               # descripción abstracta de la respuesta
    abstraction_level: Mapped[int] = mapped_column(Integer, default=4)                    # 4 = universal (por encima de principio=3)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    source_tenant_count: Mapped[int] = mapped_column(Integer, default=0)                  # cuántos tenants contribuyeron
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending_approval")               # pending_approval|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ─────────────────────────────────────────────────────────────────────────────
# FASE EXPERIMENTAL — USER ISOLATION + CORE BRAIN
# Cada usuario tiene su propio espacio cognitivo privado.
# El CoreBrain agrega sin retroalimentar (write-only desde instancias).
# ─────────────────────────────────────────────────────────────────────────────

class UserEpisode(Base):
    """
    Episodio cognitivo privado de un usuario.
    Espejo de AreaEpisode pero scoped por user_id.
    Ningún otro usuario puede leer ni influir en estos episodios.
    """
    __tablename__ = "user_episodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    situation: Mapped[str] = mapped_column(Text)
    strategy: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    session_arc: Mapped[str] = mapped_column(String)                                      # resolved|degraded|neutral|abandoned
    situation_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)          # JSON float array
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    temporal_weight: Mapped[float] = mapped_column(Float, default=1.0)
    causal_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotional_intensity: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    extraction_status: Mapped[str] = mapped_column(String, default="completed")
    # Marca si este episodio ya fue promovido al CoreBrain
    promoted_to_core: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserPattern(Base):
    """
    Patrón cognitivo privado de un usuario.
    Detectado desde sus propios UserEpisodes. Invisible para otros usuarios.
    """
    __tablename__ = "user_patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    trigger_description: Mapped[str] = mapped_column(Text)
    trigger_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_description: Mapped[str] = mapped_column(Text)
    response_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    causal_mechanism: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.3)
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    abstraction_level: Mapped[int] = mapped_column(Integer, default=1)
    is_failure_pattern: Mapped[bool] = mapped_column(Boolean, default=False)
    source_episode_ids: Mapped[str] = mapped_column(Text, default="[]")                   # JSON array de UserEpisode IDs
    # Marca si este patrón ya fue promovido al CoreBrain
    promoted_to_core: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CoreBrainEntry(Base):
    """
    Núcleo ciego — agrega conocimiento de todas las instancias de usuario.

    REGLAS:
    - Write-only desde UserBrain (ninguna instancia lee de aquí)
    - Solo el owner/investigador puede leer el CoreBrain completo
    - NUNCA almacena user_id — el origen es anónimo incluso para el investigador
    - Almacena patrones emergentes que ningún usuario individual generó solo
    - status: pending_emergence | emerged | dismissed
    """
    __tablename__ = "core_brain_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    # Sin user_id — el origen es anónimo por diseño
    trigger_description: Mapped[str] = mapped_column(Text)                                # descripción abstracta del trigger
    trigger_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_description: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    # Cuántas instancias de usuario contribuyeron (sin revelar quiénes)
    contributor_count: Mapped[int] = mapped_column(Integer, default=1)
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    # Señal de emergencia: patrón que aparece en múltiples instancias independientes
    emergence_score: Mapped[float] = mapped_column(Float, default=0.0)                    # 0-1: contributor_count / total_users
    status: Mapped[str] = mapped_column(String, default="pending_emergence")              # pending_emergence|emerged|dismissed
    # Cadena temporal si aplica (detectada cross-instancias)
    temporal_signal: Mapped[str | None] = mapped_column(Text, nullable=True)              # descripción del patrón temporal emergente
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
