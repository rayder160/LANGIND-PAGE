"""
Selective Attention — Modula la intensidad emocional de los episodios y ajusta
los parámetros de relevancia y olvido en función de la carga emocional.

Verificar settings.CME_ENABLE_SELECTIVE_ATTENTION antes de ejecutar.
"""
import logging
from app.config import settings

logger = logging.getLogger(__name__)

# Señales de frustración en mensajes
FRUSTRATION_SIGNALS = [
    "no entiendes", "no entendiste", "eso no es", "no es lo que",
    "no sirve", "no funciona", "mal", "incorrecto", "equivocado",
    "no me ayuda", "inútil", "pésimo", "terrible", "horrible",
]


class SelectiveAttention:

    def compute_emotional_intensity(
        self,
        episode,
        messages: list,
        db=None
    ) -> float:
        """
        Calcula la intensidad emocional de un episodio [0, 1].

        Reglas de acumulación:
        +0.3 si hay frustration_signals en mensajes
        +0.2 si explicit negative feedback (thumbs_down)
        +0.2 si session_arc = abandoned
        +0.4 si session_arc = resolved después de degraded (breakthrough)
        +0.2 si quality_score < 0.2 o > 0.9

        Resultado capped a 1.0.
        """
        if not settings.CME_ENABLE_SELECTIVE_ATTENTION:
            return 0.0

        try:
            intensity = 0.0

            # Verificar señales de frustración en mensajes
            if messages:
                all_text = " ".join(
                    m.content.lower() if hasattr(m, "content") else str(m).lower()
                    for m in messages
                    if hasattr(m, "role") and getattr(m, "role", "") == "user"
                )
                if any(signal in all_text for signal in FRUSTRATION_SIGNALS):
                    intensity += 0.3

            # Verificar feedback negativo explícito
            # (thumbs_down se detecta via quality_score < 0.3 como proxy)
            if episode and episode.quality_score is not None:
                if episode.quality_score < 0.3:
                    intensity += 0.2

            # session_arc = abandoned
            if episode and episode.session_arc == "abandoned":
                intensity += 0.2

            # Breakthrough: resolved después de degraded
            # Se detecta si el arc es resolved pero hay señales de frustración previas
            if episode and episode.session_arc == "resolved" and intensity > 0.0:
                intensity += 0.4

            # quality_score extremo (muy bajo o muy alto)
            if episode and episode.quality_score is not None:
                if episode.quality_score < 0.2 or episode.quality_score > 0.9:
                    intensity += 0.2

            return min(1.0, intensity)

        except Exception as e:
            logger.debug(f"CME SelectiveAttention: error en compute_emotional_intensity: {e}")
            return 0.0

    def apply_emotional_decay_modifier(
        self,
        lambda_rate: float,
        emotional_intensity: float
    ) -> float:
        """
        Ajusta la tasa de olvido según la intensidad emocional.
        Los episodios emocionalmente intensos se olvidan más lentamente.

        effective_λ = λ × (1 - emotional_intensity × 0.5)
        """
        if not settings.CME_ENABLE_SELECTIVE_ATTENTION:
            return lambda_rate

        effective_lambda = lambda_rate * (1.0 - emotional_intensity * 0.5)
        return max(0.0, effective_lambda)

    def apply_relevance_boost(
        self,
        cosine: float,
        temporal_weight: float,
        emotional_intensity: float
    ) -> float:
        """
        Aplica un boost de relevancia basado en la intensidad emocional.
        Los episodios emocionalmente intensos son más relevantes.

        relevance = cosine × temporal_weight × (1 + emotional_intensity × 0.3)
        """
        if not settings.CME_ENABLE_SELECTIVE_ATTENTION:
            return cosine * temporal_weight

        relevance = cosine * temporal_weight * (1.0 + emotional_intensity * 0.3)
        return min(1.0, relevance)


# Instancia global singleton
selective_attention = SelectiveAttention()
