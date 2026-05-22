# Re-exporta todos los modelos para compatibilidad con imports existentes
from app.models.base import Base
from app.models.tenant import Tenant
from app.models.area import Area
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.models.analytics import UserAnalytics, AreaActivityLog, MessageFeedback
from app.models.knowledge import AreaChunk, AreaDocument, WorkspaceDocument
from app.models.cme import (
    AreaEpisode,
    AreaPattern,
    AreaMethodology,
    AreaConceptEdge,
    AreaKnowledgeGap,
    AreaContradiction,
    GlobalPattern,
    GlobalMethodology,
    RLHFDataset,
    SynthesisReport,
    ProactiveAlert,
    UserCognitiveProfile,
    ConsolidationLog,
    AgentDrive,
    AgentIdentity,
    CuriosityQueue,
    CrossDomainInsight,
    TemporalChain,
    SimulationLog,
    UniversalPattern,
)

__all__ = [
    "Base", "Tenant", "Area", "User",
    "ChatSession", "ChatMessage",
    "UserAnalytics", "AreaActivityLog", "MessageFeedback",
    "AreaChunk", "AreaDocument", "WorkspaceDocument",
    # CME — Fase 1
    "AreaEpisode", "AreaPattern", "AreaMethodology", "AreaConceptEdge",
    "AreaKnowledgeGap", "AreaContradiction", "GlobalPattern", "GlobalMethodology",
    "RLHFDataset", "SynthesisReport", "ProactiveAlert", "UserCognitiveProfile",
    "ConsolidationLog",
    # CME — Fase 2
    "AgentDrive", "AgentIdentity", "CuriosityQueue",
    # CME — Fase 3
    "CrossDomainInsight", "TemporalChain", "SimulationLog",
    # CME — Universal Brain
    "UniversalPattern",
]
