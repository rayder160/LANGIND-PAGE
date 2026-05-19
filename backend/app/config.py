from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    LLM_API_URL: str
    LLM_API_KEY: str
    LLM_MODEL: str = "gemma3:4b"
    LLM_FALLBACK: str = "llama3.2:3b"

    DATABASE_URL: str

    # ─── CME — Cognitive Memory Engine ───────────────────────────────────────
    # Modo de aprobación: "auto" o "manual"
    CME_APPROVAL_MODE: Literal["auto", "manual"] = "auto"

    # Umbral de confianza para auto-aprobación de patrones y metodologías
    CME_AUTO_APPROVE_THRESHOLD: float = 0.65

    # Umbral para auto-promoción al Global Brain
    CME_GLOBAL_PROMOTE_THRESHOLD: float = 0.75
    CME_GLOBAL_PROMOTE_MIN_USERS: int = 2

    # Parámetros de aprendizaje
    CME_DEFAULT_LAMBDA: float = 0.01
    CME_PATTERN_DETECTION_INTERVAL: int = 10
    CME_RLHF_QUALITY_THRESHOLD: float = 0.80
    CME_METHODOLOGY_QUALITY_THRESHOLD: float = 0.75

    # Módulos experimentales — Fase 2
    CME_ENABLE_AGENT_DRIVES: bool = True
    CME_ENABLE_AGENT_IDENTITY: bool = True
    CME_ENABLE_SELECTIVE_ATTENTION: bool = True
    CME_ENABLE_ACTIVE_ANTICIPATION: bool = True
    CME_ENABLE_ASYMMETRIC_LEARNING: bool = True
    CME_ENABLE_GENERATIVE_CURIOSITY: bool = True

    # Identidad del agente — valores por defecto
    CME_IDENTITY_DEFAULT_NAME: str = "IM"
    CME_IDENTITY_DEFAULT_VALUES: str = '["precisión", "aprendizaje continuo", "utilidad", "honestidad"]'
    CME_IDENTITY_AUTO_ENABLE: bool = True

    # Módulos experimentales — Fase 3
    CME_ENABLE_CROSS_DOMAIN_INSIGHTS: bool = True
    CME_ENABLE_TEMPORAL_NARRATIVE: bool = True
    CME_ENABLE_MENTAL_SIMULATION: bool = False
    CME_ENABLE_UNIVERSAL_BRAIN: bool = False

    # ── Fase Experimental: Aislamiento Cognitivo por Usuario ─────────────────
    # Cuando está activo, cada usuario tiene su propio espacio cognitivo privado.
    # Los episodios y patrones se guardan en user_episodes / user_patterns.
    # El Context Enricher solo consulta la instancia del usuario actual.
    # El CoreBrain agrega sin retroalimentar (write-only desde instancias).
    # Ningún usuario ve el conocimiento de otro — ni directa ni indirectamente.
    CME_EXPERIMENTAL_USER_ISOLATION: bool = False

    # Umbral de emergencia: fracción de usuarios que deben compartir un patrón
    # para que sea considerado "emergente" en el CoreBrain (ej: 0.5 = mitad del equipo)
    CME_CORE_EMERGENCE_THRESHOLD: float = 0.5

    # Umbral de confianza mínimo para promover un UserPattern al CoreBrain
    CME_CORE_PROMOTE_MIN_CONFIDENCE: float = 0.65

    class Config:
        env_file = ".env"


settings = Settings()
