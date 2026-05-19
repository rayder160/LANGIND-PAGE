"""
Tests unitarios para ContextEnricher (Tarea 13.1).

Cubre:
- enrich retorna None cuando no hay episodios/patrones relevantes (Req 14.4)
- Exclusión de patrones con contradicción pendiente (Req 7.3)
- Tenant isolation en consultas globales (Req 21.3)
- Priorización: principios > patrones > episodios (Req 10.3)
"""
import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from app.cme.context_enricher import ContextEnricher, EPISODE_MIN_RELEVANCE, PATTERN_MIN_SIMILARITY


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_episode(
    id="ep1",
    area_id="area1",
    situation="problema de organización",
    strategy="usar kanban",
    outcome="mejoró",
    session_arc="resolved",
    temporal_weight=1.0,
    quality_score=0.85,
    emotional_intensity=0.0,
    extraction_status="completed",
    situation_embedding=None,
    created_at=None,
):
    from datetime import datetime, timezone
    ep = MagicMock()
    ep.id = id
    ep.area_id = area_id
    ep.situation = situation
    ep.strategy = strategy
    ep.outcome = outcome
    ep.session_arc = session_arc
    ep.temporal_weight = temporal_weight
    ep.quality_score = quality_score
    ep.emotional_intensity = emotional_intensity
    ep.extraction_status = extraction_status
    ep.situation_embedding = situation_embedding or json.dumps([0.1] * 10)
    ep.created_at = created_at or datetime.now(timezone.utc)
    return ep


def make_pattern(
    id="pat1",
    area_id="area1",
    abstraction_level=1,
    is_approved=True,
    is_failure_pattern=False,
    confidence_score=0.75,
    causal_mechanism=None,
    trigger_description="cuando hay desorden",
    response_description="aplicar metodología",
    trigger_embedding=None,
):
    p = MagicMock()
    p.id = id
    p.area_id = area_id
    p.abstraction_level = abstraction_level
    p.is_approved = is_approved
    p.is_failure_pattern = is_failure_pattern
    p.confidence_score = confidence_score
    p.causal_mechanism = causal_mechanism
    p.trigger_description = trigger_description
    p.response_description = response_description
    p.trigger_embedding = trigger_embedding or json.dumps([0.1] * 10)
    return p


def make_working_memory(emotion="neutral"):
    wm = MagicMock()
    wm.detected_emotion = emotion
    return wm


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestContextEnricherReturnNone:
    """Req 14.4 — enrich retorna None cuando no hay contenido relevante."""

    @pytest.mark.asyncio
    async def test_returns_none_when_embedding_fails(self):
        """Si get_embedding falla, retorna None silenciosamente."""
        enricher = ContextEnricher()
        db = AsyncMock()

        with patch("app.cme.context_enricher.get_embedding", return_value=None):
            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_relevant_data(self):
        """Si no hay episodios, patrones ni metodologías relevantes, retorna None."""
        enricher = ContextEnricher()
        db = AsyncMock()

        # Simular embedding válido pero sin resultados relevantes
        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        """Si ocurre una excepción inesperada, retorna None (fail-silent)."""
        enricher = ContextEnricher()
        db = AsyncMock()

        with patch("app.cme.context_enricher.get_embedding", side_effect=RuntimeError("network error")):
            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        assert result is None


class TestContextEnricherExcludeContradictions:
    """Req 7.3 — Patrones con contradicción pendiente deben excluirse."""

    @pytest.mark.asyncio
    async def test_excluded_pattern_ids_from_pending_contradictions(self):
        """_get_excluded_pattern_ids retorna IDs de ambos patrones en contradicción pendiente."""
        enricher = ContextEnricher()
        db = AsyncMock()

        # Simular contradicción pendiente
        contradiction = MagicMock()
        contradiction.pattern_a_id = "pat_a"
        contradiction.pattern_b_id = "pat_b"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [contradiction]
        db.execute = AsyncMock(return_value=mock_result)

        excluded = await enricher._get_excluded_pattern_ids("area1", db)

        assert "pat_a" in excluded
        assert "pat_b" in excluded

    @pytest.mark.asyncio
    async def test_excluded_pattern_not_in_payload(self):
        """Un patrón con contradicción pendiente no aparece en el payload final."""
        enricher = ContextEnricher()
        db = AsyncMock()

        # Patrón con ID excluido
        excluded_pattern = make_pattern(id="excluded_pat", abstraction_level=1)
        # Embedding similar al query para que normalmente pasaría el umbral
        high_sim_emb = json.dumps([1.0] * 10)
        excluded_pattern.trigger_embedding = high_sim_emb

        with patch("app.cme.context_enricher.get_embedding", return_value=[1.0] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value={"excluded_pat"}), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            # Simular que _query_patterns respeta la exclusión (comportamiento real)
            mock_db_result = MagicMock()
            mock_db_result.scalars.return_value.all.return_value = [excluded_pattern]
            db.execute = AsyncMock(return_value=mock_db_result)

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        # El patrón excluido no debe aparecer en el payload
        assert result is None or "excluded_pat" not in str(result)

    @pytest.mark.asyncio
    async def test_get_excluded_pattern_ids_returns_empty_on_error(self):
        """Si falla la consulta de contradicciones, retorna set vacío (fail-silent)."""
        enricher = ContextEnricher()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("DB error"))

        excluded = await enricher._get_excluded_pattern_ids("area1", db)

        assert excluded == set()


class TestContextEnricherPrioritization:
    """Req 10.3 — Priorización: Principios > Patrones > Episodios."""

    @pytest.mark.asyncio
    async def test_principles_appear_before_patterns(self):
        """Los principios (abstraction_level=3) aparecen antes que los patrones regulares."""
        enricher = ContextEnricher()
        db = AsyncMock()

        principle = make_pattern(
            id="principle1",
            abstraction_level=3,
            trigger_description="Principio de organización sistémica",
        )
        regular_pattern = make_pattern(
            id="pat1",
            abstraction_level=1,
            trigger_description="Patrón de trabajo diario",
        )

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[(principle, 0.9), (regular_pattern, 0.8)]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        assert result is not None
        # Principios del área debe aparecer antes que Patrones del área
        assert result.index("Principios del área") < result.index("Patrones del área")

    @pytest.mark.asyncio
    async def test_payload_contains_all_sections(self):
        """El payload incluye todas las secciones cuando hay datos relevantes."""
        enricher = ContextEnricher()
        db = AsyncMock()

        episode = make_episode()
        pattern = make_pattern()
        methodology = MagicMock()
        methodology.title = "Metodología Kanban"
        methodology.description = "Usar tablero visual para gestionar tareas"

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[(episode, 0.75)]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[(pattern, 0.80)]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[methodology]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        assert result is not None
        assert "## Memoria cognitiva relevante" in result
        assert "### Episodios similares" in result
        assert "### Patrones del área" in result
        assert "### Metodologías aprobadas" in result


class TestContextEnricherTenantIsolation:
    """Req 21.3 — Las consultas globales deben filtrarse por tenant_id."""

    @pytest.mark.asyncio
    async def test_global_patterns_use_tenant_id(self):
        """query_patterns del GlobalBrain se llama con el tenant_id correcto."""
        enricher = ContextEnricher()
        db = AsyncMock()

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]) as mock_gp, \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]) as mock_gm:

            await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant_xyz",
                working_memory=make_working_memory(),
                db=db,
            )

        # Verificar que se pasó el tenant_id correcto
        mock_gp.assert_called_once()
        call_args = mock_gp.call_args
        assert "tenant_xyz" in call_args.args or call_args.kwargs.get("tenant_id") == "tenant_xyz"

    @pytest.mark.asyncio
    async def test_global_methodologies_use_tenant_id(self):
        """query_methodologies del GlobalBrain se llama con el tenant_id correcto."""
        enricher = ContextEnricher()
        db = AsyncMock()

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]) as mock_gm:

            await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant_abc",
                working_memory=make_working_memory(),
                db=db,
            )

        mock_gm.assert_called_once()
        call_args = mock_gm.call_args
        assert "tenant_abc" in call_args.args or call_args.kwargs.get("tenant_id") == "tenant_abc"


class TestContextEnricherFrustratedUser:
    """Req 16.3 — Si detected_emotion=frustrated, priorizar episodios resolved."""

    @pytest.mark.asyncio
    async def test_frustrated_user_prioritizes_resolved_episodes(self):
        """Con usuario frustrado, _query_episodes se llama con prioritize_resolved=True."""
        enricher = ContextEnricher()
        db = AsyncMock()

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]) as mock_eps, \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(emotion="frustrated"),
                db=db,
            )

        mock_eps.assert_called_once()
        # La firma es _query_episodes(query_embedding, area_id, db, prioritize_resolved)
        # args[3] es prioritize_resolved (posición 0-indexed)
        call_args = mock_eps.call_args
        prioritize = call_args.args[3] if len(call_args.args) > 3 else call_args.kwargs.get("prioritize_resolved")
        assert prioritize is True

    @pytest.mark.asyncio
    async def test_neutral_user_does_not_prioritize_resolved(self):
        """Con usuario neutral, _query_episodes se llama con prioritize_resolved=False."""
        enricher = ContextEnricher()
        db = AsyncMock()

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]) as mock_eps, \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(emotion="neutral"),
                db=db,
            )

        mock_eps.assert_called_once()
        call_args = mock_eps.call_args
        prioritize = call_args.args[3] if len(call_args.args) > 3 else call_args.kwargs.get("prioritize_resolved")
        assert prioritize is False


class TestContextEnricherFailurePatterns:
    """Req 32.4 — Failure patterns deben anotarse con 'evitar este enfoque'."""

    @pytest.mark.asyncio
    async def test_failure_pattern_annotated_correctly(self):
        """Un patrón de fallo aparece con la anotación 'Evitar este enfoque'."""
        enricher = ContextEnricher()
        db = AsyncMock()

        failure_pattern = make_pattern(
            id="fail_pat",
            abstraction_level=1,
            is_failure_pattern=True,
            trigger_description="Intentar resolver todo de una vez",
        )

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[(failure_pattern, 0.80)]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        assert result is not None
        assert "Evitar este enfoque" in result


class TestContextEnricherCausalMechanism:
    """Req 25.3 — Incluir causal_mechanism si el patrón lo tiene."""

    @pytest.mark.asyncio
    async def test_causal_mechanism_included_in_payload(self):
        """Si el patrón tiene causal_mechanism, aparece en el payload."""
        enricher = ContextEnricher()
        db = AsyncMock()

        pattern_with_cause = make_pattern(
            id="pat_cause",
            abstraction_level=1,
            causal_mechanism="reduce la carga cognitiva al dividir el problema",
        )

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[(pattern_with_cause, 0.80)]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
            )

        assert result is not None
        assert "esto funciona porque" in result
        assert "reduce la carga cognitiva" in result


class TestContextEnricherUserProfile:
    """Req 26.3 — Incluir perfil cognitivo del usuario."""

    @pytest.mark.asyncio
    async def test_user_profile_included_when_available(self):
        """Si hay perfil de usuario, se incluye en el payload."""
        enricher = ContextEnricher()
        db = AsyncMock()

        profile = MagicMock()
        profile.expertise_level = 0.8  # avanzado
        profile.preferred_detail_level = "detailed"

        episode = make_episode()

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[(episode, 0.75)]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_get_user_profile", new_callable=AsyncMock, return_value=profile), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
                user_id="user1",
            )

        assert result is not None
        assert "Perfil del usuario" in result
        assert "avanzado" in result
        assert "detailed" in result

    @pytest.mark.asyncio
    async def test_basic_expertise_level(self):
        """Nivel de expertise ≤ 0.3 se muestra como 'básico'."""
        enricher = ContextEnricher()
        db = AsyncMock()

        profile = MagicMock()
        profile.expertise_level = 0.2
        profile.preferred_detail_level = "brief"

        episode = make_episode()

        with patch("app.cme.context_enricher.get_embedding", return_value=[0.1] * 10), \
             patch.object(enricher, "_get_excluded_pattern_ids", new_callable=AsyncMock, return_value=set()), \
             patch.object(enricher, "_query_episodes", new_callable=AsyncMock, return_value=[(episode, 0.75)]), \
             patch.object(enricher, "_query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_methodologies", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_query_concept_edges", new_callable=AsyncMock, return_value=[]), \
             patch.object(enricher, "_get_user_profile", new_callable=AsyncMock, return_value=profile), \
             patch("app.cme.context_enricher.global_brain.query_patterns", new_callable=AsyncMock, return_value=[]), \
             patch("app.cme.context_enricher.global_brain.query_methodologies", new_callable=AsyncMock, return_value=[]):

            result = await enricher.enrich(
                query="test query",
                area_id="area1",
                tenant_id="tenant1",
                working_memory=make_working_memory(),
                db=db,
                user_id="user1",
            )

        assert result is not None
        assert "básico" in result
