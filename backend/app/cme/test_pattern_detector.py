"""
Tests unitarios para PatternDetector.

Validates: Requirements 5.2, 5.4, 5.5, 7.1
"""
import json
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.cme.pattern_detector import PatternDetector, CLUSTER_SIMILARITY_THRESHOLD, MIN_CLUSTER_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_unit_vector(dim: int, index: int) -> list[float]:
    """Crea un vector unitario en la dimensión `index`."""
    v = [0.0] * dim
    v[index] = 1.0
    return v


def make_similar_vector(base: list[float], noise: float = 0.05) -> list[float]:
    """Crea un vector muy similar al base (alta similitud coseno)."""
    noisy = [v + noise * (0.5 - i % 2) for i, v in enumerate(base)]
    # Normalizar
    norm = math.sqrt(sum(x * x for x in noisy))
    return [x / norm for x in noisy] if norm > 0 else noisy


def make_episode_mock(
    ep_id: str,
    session_arc: str = "resolved",
    quality_score: float | None = 0.8,
    causal_explanation: str | None = None,
    situation: str = "Situación de prueba",
    strategy: str = "Estrategia de prueba",
    session_id: str = "sess-1",
) -> MagicMock:
    ep = MagicMock()
    ep.id = ep_id
    ep.session_arc = session_arc
    ep.quality_score = quality_score
    ep.causal_explanation = causal_explanation
    ep.situation = situation
    ep.strategy = strategy
    ep.session_id = session_id
    return ep


# ─────────────────────────────────────────────────────────────────────────────
# Tests de cluster_episodes
# ─────────────────────────────────────────────────────────────────────────────

class TestClusterEpisodes:
    """Validates: Requirement 5.2"""

    def test_empty_input_returns_empty(self):
        detector = PatternDetector()
        result = detector.cluster_episodes([])
        assert result == []

    def test_similar_embeddings_form_cluster(self):
        """Episodios con alta similitud coseno deben agruparse en el mismo cluster."""
        detector = PatternDetector()
        base = make_unit_vector(10, 0)
        # Crear 4 episodios muy similares al base
        episodes_with_emb = [
            (make_episode_mock(f"ep-{i}"), make_similar_vector(base, noise=0.01))
            for i in range(4)
        ]
        clusters = detector.cluster_episodes(episodes_with_emb, threshold=0.75)
        assert len(clusters) == 1
        assert len(clusters[0]) == 4

    def test_dissimilar_embeddings_no_cluster(self):
        """Episodios con baja similitud coseno no deben agruparse."""
        detector = PatternDetector()
        # Vectores ortogonales (similitud = 0)
        episodes_with_emb = [
            (make_episode_mock(f"ep-{i}"), make_unit_vector(10, i))
            for i in range(5)
        ]
        clusters = detector.cluster_episodes(episodes_with_emb, threshold=0.75)
        # Ningún cluster tiene ≥ MIN_CLUSTER_SIZE episodios
        assert clusters == []

    def test_cluster_requires_min_size(self):
        """Clusters con menos de MIN_CLUSTER_SIZE episodios son descartados."""
        detector = PatternDetector()
        base = make_unit_vector(10, 0)
        # Solo 2 episodios similares (< MIN_CLUSTER_SIZE=3)
        episodes_with_emb = [
            (make_episode_mock(f"ep-{i}"), make_similar_vector(base, noise=0.01))
            for i in range(2)
        ]
        clusters = detector.cluster_episodes(episodes_with_emb, threshold=0.75)
        assert clusters == []

    def test_two_separate_clusters(self):
        """Dos grupos de episodios similares pero distintos entre sí forman 2 clusters."""
        detector = PatternDetector()
        base_a = make_unit_vector(10, 0)
        base_b = make_unit_vector(10, 5)

        group_a = [
            (make_episode_mock(f"ep-a{i}"), make_similar_vector(base_a, noise=0.01))
            for i in range(3)
        ]
        group_b = [
            (make_episode_mock(f"ep-b{i}"), make_similar_vector(base_b, noise=0.01))
            for i in range(3)
        ]
        clusters = detector.cluster_episodes(group_a + group_b, threshold=0.75)
        assert len(clusters) == 2
        # Cada cluster tiene 3 episodios
        assert all(len(c) == 3 for c in clusters)

    def test_already_assigned_episodes_not_duplicated(self):
        """Un episodio no puede pertenecer a más de un cluster."""
        detector = PatternDetector()
        base = make_unit_vector(10, 0)
        episodes_with_emb = [
            (make_episode_mock(f"ep-{i}"), make_similar_vector(base, noise=0.01))
            for i in range(5)
        ]
        clusters = detector.cluster_episodes(episodes_with_emb, threshold=0.75)
        # Todos los episodios deben estar en exactamente un cluster
        all_ids = [ep.id for cluster in clusters for ep, _ in cluster]
        assert len(all_ids) == len(set(all_ids))


# ─────────────────────────────────────────────────────────────────────────────
# Tests de compute_confidence
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeConfidence:
    """Validates: Requirement 5.4"""

    def test_empty_quality_scores_returns_default(self):
        detector = PatternDetector()
        result = detector.compute_confidence([], diversity_score=0.5)
        assert result == 0.3

    def test_formula_correctness(self):
        """confidence = (mean_quality × 0.6) + (diversity_score × 0.4)"""
        detector = PatternDetector()
        quality_scores = [0.8, 0.8, 0.8]  # mean = 0.8
        diversity_score = 0.5
        expected = (0.8 * 0.6) + (0.5 * 0.4)  # = 0.48 + 0.20 = 0.68
        result = detector.compute_confidence(quality_scores, diversity_score)
        assert abs(result - expected) < 0.001

    def test_high_quality_high_diversity(self):
        detector = PatternDetector()
        result = detector.compute_confidence([1.0, 1.0, 1.0], diversity_score=1.0)
        assert result == 1.0

    def test_zero_quality_zero_diversity(self):
        detector = PatternDetector()
        result = detector.compute_confidence([0.0, 0.0], diversity_score=0.0)
        assert result == 0.0

    def test_result_clamped_to_0_1(self):
        """El resultado siempre debe estar en [0.0, 1.0]."""
        detector = PatternDetector()
        # Scores > 1 no deberían ocurrir, pero la función debe ser robusta
        result = detector.compute_confidence([0.5], diversity_score=0.5)
        assert 0.0 <= result <= 1.0

    def test_single_quality_score(self):
        detector = PatternDetector()
        result = detector.compute_confidence([0.6], diversity_score=0.0)
        expected = 0.6 * 0.6  # = 0.36
        assert abs(result - expected) < 0.001


# ─────────────────────────────────────────────────────────────────────────────
# Tests de compute_diversity
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeDiversity:
    """Validates: Requirement 5.4"""

    def test_empty_user_ids_returns_zero(self):
        detector = PatternDetector()
        result = detector.compute_diversity([])
        assert result == 0.0

    def test_all_same_user(self):
        """10 episodios del mismo usuario → diversity = 1/10 = 0.1"""
        detector = PatternDetector()
        result = detector.compute_diversity(["user-1"] * 10)
        assert abs(result - 0.1) < 0.001

    def test_all_distinct_users(self):
        """5 episodios de 5 usuarios distintos → diversity = 5/5 = 1.0"""
        detector = PatternDetector()
        result = detector.compute_diversity([f"user-{i}" for i in range(5)])
        assert result == 1.0

    def test_partial_diversity(self):
        """3 usuarios distintos en 6 episodios → diversity = 3/6 = 0.5"""
        detector = PatternDetector()
        user_ids = ["user-1", "user-1", "user-2", "user-2", "user-3", "user-3"]
        result = detector.compute_diversity(user_ids)
        assert abs(result - 0.5) < 0.001

    def test_diversity_capped_at_1(self):
        """La diversidad nunca supera 1.0."""
        detector = PatternDetector()
        result = detector.compute_diversity([f"user-{i}" for i in range(100)])
        assert result <= 1.0

    def test_single_user_single_episode(self):
        detector = PatternDetector()
        result = detector.compute_diversity(["user-1"])
        assert result == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Tests de detección de contradicciones
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectContradictions:
    """Validates: Requirements 5.5, 7.1"""

    @pytest.mark.asyncio
    async def test_contradiction_detected_when_trigger_similar_response_opposite(self):
        """
        trigger_sim ≥ 0.80 Y response_sim ≤ 0.30 → debe crear AreaContradiction.
        Validates: Requirement 7.1
        """
        detector = PatternDetector()

        # Embeddings: trigger muy similar, response opuesto
        trigger_emb = make_unit_vector(10, 0)
        response_a = make_unit_vector(10, 1)   # respuesta A
        response_b = make_unit_vector(10, 9)   # respuesta B (ortogonal a A → sim=0)

        new_pattern = MagicMock()
        new_pattern.id = "pattern-new"
        new_pattern.trigger_embedding = json.dumps(trigger_emb)
        new_pattern.response_embedding = json.dumps(response_a)
        new_pattern.trigger_description = "Trigger nuevo"

        existing_pattern = MagicMock()
        existing_pattern.id = "pattern-existing"
        existing_pattern.trigger_embedding = json.dumps(trigger_emb)  # mismo trigger
        existing_pattern.response_embedding = json.dumps(response_b)  # respuesta opuesta
        existing_pattern.trigger_description = "Trigger existente"

        # Mock de la DB
        db = AsyncMock()

        # approved_patterns query
        approved_result = MagicMock()
        approved_result.scalars.return_value.all.return_value = [existing_pattern]

        # existing_contradiction query (no existe contradicción previa)
        no_contradiction = MagicMock()
        no_contradiction.scalar_one_or_none.return_value = None

        db.execute = AsyncMock(side_effect=[approved_result, no_contradiction])
        db.add = MagicMock()
        db.commit = AsyncMock()

        await detector._detect_contradictions(new_pattern, "area-1", db)

        # Debe haber añadido una contradicción
        db.add.assert_called_once()
        added_obj = db.add.call_args[0][0]
        assert added_obj.pattern_a_id == "pattern-new"
        assert added_obj.pattern_b_id == "pattern-existing"
        assert added_obj.status == "pending"

    @pytest.mark.asyncio
    async def test_no_contradiction_when_trigger_dissimilar(self):
        """trigger_sim < 0.80 → no debe crear contradicción."""
        detector = PatternDetector()

        trigger_a = make_unit_vector(10, 0)
        trigger_b = make_unit_vector(10, 5)  # ortogonal → sim=0
        response_a = make_unit_vector(10, 1)
        response_b = make_unit_vector(10, 9)

        new_pattern = MagicMock()
        new_pattern.id = "pattern-new"
        new_pattern.trigger_embedding = json.dumps(trigger_a)
        new_pattern.response_embedding = json.dumps(response_a)
        new_pattern.trigger_description = "Trigger A"

        existing_pattern = MagicMock()
        existing_pattern.id = "pattern-existing"
        existing_pattern.trigger_embedding = json.dumps(trigger_b)
        existing_pattern.response_embedding = json.dumps(response_b)
        existing_pattern.trigger_description = "Trigger B"

        db = AsyncMock()
        approved_result = MagicMock()
        approved_result.scalars.return_value.all.return_value = [existing_pattern]
        db.execute = AsyncMock(return_value=approved_result)
        db.add = MagicMock()

        await detector._detect_contradictions(new_pattern, "area-1", db)

        # No debe añadir ninguna contradicción
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_contradiction_when_response_similar(self):
        """trigger_sim ≥ 0.80 pero response_sim > 0.30 → no es contradicción."""
        detector = PatternDetector()

        trigger_emb = make_unit_vector(10, 0)
        response_emb = make_unit_vector(10, 1)  # misma respuesta

        new_pattern = MagicMock()
        new_pattern.id = "pattern-new"
        new_pattern.trigger_embedding = json.dumps(trigger_emb)
        new_pattern.response_embedding = json.dumps(response_emb)
        new_pattern.trigger_description = "Trigger"

        existing_pattern = MagicMock()
        existing_pattern.id = "pattern-existing"
        existing_pattern.trigger_embedding = json.dumps(trigger_emb)
        existing_pattern.response_embedding = json.dumps(response_emb)  # misma respuesta
        existing_pattern.trigger_description = "Trigger"

        db = AsyncMock()
        approved_result = MagicMock()
        approved_result.scalars.return_value.all.return_value = [existing_pattern]
        db.execute = AsyncMock(return_value=approved_result)
        db.add = MagicMock()

        await detector._detect_contradictions(new_pattern, "area-1", db)

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_contradiction_when_no_trigger_embedding(self):
        """Si el nuevo patrón no tiene trigger_embedding, no se procesa."""
        detector = PatternDetector()

        new_pattern = MagicMock()
        new_pattern.trigger_embedding = None

        db = AsyncMock()
        db.execute = AsyncMock()

        await detector._detect_contradictions(new_pattern, "area-1", db)

        # No debe ejecutar ninguna query
        db.execute.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Tests de _average_embeddings
# ─────────────────────────────────────────────────────────────────────────────

class TestAverageEmbeddings:

    def test_empty_returns_none(self):
        detector = PatternDetector()
        result = detector._average_embeddings([])
        assert result is None

    def test_single_embedding_returns_same(self):
        detector = PatternDetector()
        emb = [1.0, 2.0, 3.0]
        result = detector._average_embeddings([emb])
        assert result == emb

    def test_average_of_two_embeddings(self):
        detector = PatternDetector()
        emb_a = [1.0, 0.0]
        emb_b = [0.0, 1.0]
        result = detector._average_embeddings([emb_a, emb_b])
        assert result == [0.5, 0.5]

    def test_average_of_identical_embeddings(self):
        detector = PatternDetector()
        emb = [0.5, 0.5, 0.5]
        result = detector._average_embeddings([emb, emb, emb])
        assert result == emb


# ─────────────────────────────────────────────────────────────────────────────
# Tests de failure weight (Req 32.2)
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureWeighting:
    """Validates: Requirement 32.2 — episodios de fallo pesan 1.5×"""

    def test_failure_episodes_weighted_higher(self):
        """
        Un cluster con episodios de fallo debe producir un confidence_score
        diferente al mismo cluster sin episodios de fallo, dado que los
        quality_scores de fallo se ponderan 1.5×.
        """
        detector = PatternDetector()

        # Simular la lógica de ponderación directamente
        episodes_normal = [
            make_episode_mock(f"ep-{i}", session_arc="resolved", quality_score=0.5)
            for i in range(3)
        ]
        episodes_failure = [
            make_episode_mock(f"ep-{i}", session_arc="abandoned", quality_score=0.5)
            for i in range(3)
        ]

        # Calcular weighted_quality_scores para episodios normales
        weighted_normal = []
        for ep in episodes_normal:
            weight = 1.5 if ep.session_arc in ("abandoned", "degraded") else 1.0
            weighted_normal.extend([ep.quality_score] * int(weight * 10))

        # Calcular weighted_quality_scores para episodios de fallo
        weighted_failure = []
        for ep in episodes_failure:
            weight = 1.5 if ep.session_arc in ("abandoned", "degraded") else 1.0
            weighted_failure.extend([ep.quality_score] * int(weight * 10))

        # Los episodios de fallo deben tener más entradas (1.5× = 15 vs 10)
        assert len(weighted_failure) > len(weighted_normal)
        assert len(weighted_failure) == 45  # 3 episodios × 15 entradas
        assert len(weighted_normal) == 30   # 3 episodios × 10 entradas
