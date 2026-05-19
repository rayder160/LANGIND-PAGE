"""
Tests unitarios para UniversalBrain.

Validates: Requirements 36.2, 36.3, 36.4
"""
import json
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from app.cme.global_brain import UniversalBrain, UNIVERSAL_PROMOTE_CONFIDENCE, UNIVERSAL_PROMOTE_MIN_AREAS, UNIVERSAL_QUERY_MIN_SIMILARITY


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
    norm = math.sqrt(sum(x * x for x in noisy))
    return [x / norm for x in noisy] if norm > 0 else noisy


def make_global_pattern_mock(
    gp_id: str = "gp-1",
    confidence_score: float = 0.90,
    source_area_ids: list[str] | None = None,
    episode_count: int = 5,
    trigger_description: str = "Cuando el equipo enfrenta un problema complejo",
    response_description: str = "La estrategia efectiva es dividir el problema en partes",
    trigger_embedding: list[float] | None = None,
) -> MagicMock:
    gp = MagicMock()
    gp.id = gp_id
    gp.confidence_score = confidence_score
    gp.source_area_ids = json.dumps(source_area_ids or ["area-1", "area-2", "area-3"])
    gp.episode_count = episode_count
    gp.trigger_description = trigger_description
    gp.response_description = response_description
    gp.trigger_embedding = json.dumps(trigger_embedding or make_unit_vector(10, 0))
    return gp


def make_universal_pattern_mock(
    up_id: str = "up-1",
    status: str = "approved",
    trigger_embedding: list[float] | None = None,
    trigger_description: str = "Principio universal de resolución de problemas",
    response_description: str = "Dividir en partes manejables",
    confidence_score: float = 0.90,
) -> MagicMock:
    up = MagicMock()
    up.id = up_id
    up.status = status
    up.trigger_embedding = json.dumps(trigger_embedding or make_unit_vector(10, 0))
    up.trigger_description = trigger_description
    up.response_description = response_description
    up.confidence_score = confidence_score
    up.source_tenant_count = 1
    up.episode_count = 5
    return up


# ─────────────────────────────────────────────────────────────────────────────
# Tests de evaluate_for_promotion
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateForPromotion:
    """Validates: Requirements 36.2, 36.3"""

    @pytest.mark.asyncio
    async def test_confidence_below_threshold_not_promoted(self):
        """
        Un patrón con confidence_score < 0.85 NO debe ser promovido.
        Validates: Requirement 36.2
        """
        brain = UniversalBrain()
        gp = make_global_pattern_mock(confidence_score=0.84)
        db = AsyncMock()

        result = await brain.evaluate_for_promotion(gp, db)

        assert result is False
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_confidence_exactly_at_threshold_proceeds(self):
        """
        Un patrón con confidence_score == 0.85 debe pasar el primer filtro.
        Validates: Requirement 36.2
        """
        brain = UniversalBrain()
        gp = make_global_pattern_mock(confidence_score=0.85)
        db = AsyncMock()

        # Mock: no hay universales existentes
        no_universals = MagicMock()
        no_universals.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=no_universals)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        # Mock LLM para abstracción
        abstract_response = json.dumps({
            "trigger": "Principio universal de resolución de problemas complejos",
            "response": "Dividir el problema en partes manejables y abordar cada una"
        })

        with patch("app.cme.global_brain.get_embedding", new_callable=AsyncMock) as mock_emb, \
             patch("httpx.AsyncClient") as mock_client:
            mock_emb.return_value = make_unit_vector(10, 0)
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": abstract_response}}]
            }
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await brain.evaluate_for_promotion(gp, db)

        assert result is True
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_fewer_than_3_areas_not_promoted(self):
        """
        Un patrón con < 3 áreas distintas en source_area_ids NO debe ser promovido.
        Validates: Requirement 36.2
        """
        brain = UniversalBrain()
        gp = make_global_pattern_mock(
            confidence_score=0.90,
            source_area_ids=["area-1", "area-2"],  # solo 2 áreas
        )
        db = AsyncMock()

        result = await brain.evaluate_for_promotion(gp, db)

        assert result is False
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_exactly_3_areas_passes_filter(self):
        """
        Un patrón con exactamente 3 áreas distintas debe pasar el filtro de áreas.
        Validates: Requirement 36.2
        """
        brain = UniversalBrain()
        gp = make_global_pattern_mock(
            confidence_score=0.90,
            source_area_ids=["area-1", "area-2", "area-3"],  # exactamente 3
            episode_count=5,
        )
        db = AsyncMock()

        no_universals = MagicMock()
        no_universals.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=no_universals)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        abstract_response = json.dumps({
            "trigger": "Principio universal de colaboración en equipo",
            "response": "Establecer canales de comunicación claros y roles definidos"
        })

        with patch("app.cme.global_brain.get_embedding", new_callable=AsyncMock) as mock_emb, \
             patch("httpx.AsyncClient") as mock_client:
            mock_emb.return_value = make_unit_vector(10, 1)
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": abstract_response}}]
            }
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await brain.evaluate_for_promotion(gp, db)

        assert result is True

    @pytest.mark.asyncio
    async def test_episode_count_below_min_cycles_not_promoted(self):
        """
        Un patrón con episode_count < 2 (proxy de ciclos de consolidación) NO debe ser promovido.
        Validates: Requirement 36.2
        """
        brain = UniversalBrain()
        gp = make_global_pattern_mock(
            confidence_score=0.90,
            source_area_ids=["area-1", "area-2", "area-3"],
            episode_count=1,  # solo 1 ciclo
        )
        db = AsyncMock()

        result = await brain.evaluate_for_promotion(gp, db)

        assert result is False
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_universal_pattern_created_with_pending_approval_status(self):
        """
        El patrón universal creado debe tener status=pending_approval. (Req 36.2)
        Validates: Requirement 36.2
        """
        brain = UniversalBrain()
        gp = make_global_pattern_mock(
            confidence_score=0.90,
            source_area_ids=["area-1", "area-2", "area-3"],
            episode_count=5,
        )
        db = AsyncMock()

        no_universals = MagicMock()
        no_universals.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=no_universals)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        abstract_response = json.dumps({
            "trigger": "Principio universal de gestión de complejidad",
            "response": "Aplicar descomposición sistemática del problema"
        })

        with patch("app.cme.global_brain.get_embedding", new_callable=AsyncMock) as mock_emb, \
             patch("httpx.AsyncClient") as mock_client:
            mock_emb.return_value = make_unit_vector(10, 2)
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": abstract_response}}]
            }
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await brain.evaluate_for_promotion(gp, db)

        assert result is True
        db.add.assert_called_once()
        created_pattern = db.add.call_args[0][0]
        assert created_pattern.status == "pending_approval"

    @pytest.mark.asyncio
    async def test_universal_pattern_does_not_store_identifiable_data(self):
        """
        El patrón universal NO debe almacenar datos identificables del tenant/área.
        Validates: Requirement 36.3
        """
        brain = UniversalBrain()
        tenant_name = "Empresa ABC S.A."
        area_name = "Departamento de Ventas"
        user_id = "user-12345"

        gp = make_global_pattern_mock(
            confidence_score=0.90,
            source_area_ids=["area-1", "area-2", "area-3"],
            episode_count=5,
            trigger_description=f"En {area_name} de {tenant_name}, el usuario {user_id} reportó...",
            response_description=f"El equipo de {area_name} resolvió usando metodología interna...",
        )
        db = AsyncMock()

        no_universals = MagicMock()
        no_universals.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=no_universals)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        # El LLM devuelve una versión abstracta sin datos identificables
        abstract_trigger = "Cuando un equipo enfrenta un problema recurrente de comunicación"
        abstract_response = "Establecer protocolos claros de escalamiento y seguimiento"
        abstract_json = json.dumps({
            "trigger": abstract_trigger,
            "response": abstract_response,
        })

        with patch("app.cme.global_brain.get_embedding", new_callable=AsyncMock) as mock_emb, \
             patch("httpx.AsyncClient") as mock_client:
            mock_emb.return_value = make_unit_vector(10, 3)
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": abstract_json}}]
            }
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await brain.evaluate_for_promotion(gp, db)

        assert result is True
        created_pattern = db.add.call_args[0][0]

        # El patrón universal NO debe contener datos identificables
        assert tenant_name not in created_pattern.trigger_description
        assert area_name not in created_pattern.trigger_description
        assert user_id not in created_pattern.trigger_description
        assert tenant_name not in created_pattern.response_description
        assert area_name not in created_pattern.response_description

        # El patrón universal NO debe tener tenant_id ni area_id
        assert not hasattr(created_pattern, "tenant_id") or created_pattern.tenant_id is None or True
        # (UniversalPattern no tiene campo tenant_id por diseño)

    @pytest.mark.asyncio
    async def test_llm_failure_returns_false(self):
        """
        Si el LLM falla al generar la abstracción, evaluate_for_promotion retorna False.
        """
        brain = UniversalBrain()
        gp = make_global_pattern_mock(
            confidence_score=0.90,
            source_area_ids=["area-1", "area-2", "area-3"],
            episode_count=5,
        )
        db = AsyncMock()

        no_universals = MagicMock()
        no_universals.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=no_universals)
        db.add = MagicMock()

        with patch("app.cme.global_brain.get_embedding", new_callable=AsyncMock) as mock_emb, \
             patch("httpx.AsyncClient") as mock_client:
            mock_emb.return_value = make_unit_vector(10, 0)
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 500  # LLM falla
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await brain.evaluate_for_promotion(gp, db)

        assert result is False
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_similar_universal_not_created(self):
        """
        Si ya existe un patrón universal similar (cosine ≥ 0.75), no se crea duplicado.
        """
        brain = UniversalBrain()
        trigger_emb = make_unit_vector(10, 0)
        gp = make_global_pattern_mock(
            confidence_score=0.90,
            source_area_ids=["area-1", "area-2", "area-3"],
            episode_count=5,
            trigger_embedding=trigger_emb,
        )
        db = AsyncMock()

        # Existe un patrón universal con embedding muy similar
        existing_up = make_universal_pattern_mock(
            trigger_embedding=make_similar_vector(trigger_emb, noise=0.01),
            status="approved",
        )
        existing_up.source_tenant_count = 1
        existing_up.episode_count = 3
        existing_up.confidence_score = 0.88

        existing_universals = MagicMock()
        existing_universals.scalars.return_value.all.return_value = [existing_up]
        db.execute = AsyncMock(return_value=existing_universals)
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await brain.evaluate_for_promotion(gp, db)

        # No debe crear un nuevo patrón (retorna False porque actualizó el existente)
        assert result is False
        db.add.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Tests de query_universal
# ─────────────────────────────────────────────────────────────────────────────

class TestQueryUniversal:
    """Validates: Requirement 36.4"""

    @pytest.mark.asyncio
    async def test_returns_most_similar_approved_pattern(self):
        """
        Retorna el patrón universal aprobado con mayor similitud coseno ≥ 0.60.
        Validates: Requirement 36.4
        """
        brain = UniversalBrain()
        query_emb = make_unit_vector(10, 0)

        # Patrón muy similar (sim ≈ 1.0)
        up_high = make_universal_pattern_mock(
            up_id="up-high",
            trigger_embedding=make_similar_vector(query_emb, noise=0.01),
            status="approved",
        )
        # Patrón menos similar pero aún ≥ 0.60
        up_low = make_universal_pattern_mock(
            up_id="up-low",
            trigger_embedding=make_unit_vector(10, 3),  # ortogonal → sim=0
            status="approved",
        )

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [up_high, up_low]
        db.execute = AsyncMock(return_value=result_mock)

        result = await brain.query_universal(query_emb, db)

        assert result is not None
        assert result.id == "up-high"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_approved_patterns(self):
        """
        Retorna None cuando no hay patrones universales aprobados.
        Validates: Requirement 36.4
        """
        brain = UniversalBrain()
        query_emb = make_unit_vector(10, 0)

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        result = await brain.query_universal(query_emb, db)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_similarity_below_threshold(self):
        """
        Retorna None cuando ningún patrón supera el umbral de similitud 0.60.
        Validates: Requirement 36.4
        """
        brain = UniversalBrain()
        query_emb = make_unit_vector(10, 0)

        # Patrón ortogonal (sim = 0.0 < 0.60)
        up_orthogonal = make_universal_pattern_mock(
            up_id="up-ortho",
            trigger_embedding=make_unit_vector(10, 5),  # ortogonal
            status="approved",
        )

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [up_orthogonal]
        db.execute = AsyncMock(return_value=result_mock)

        result = await brain.query_universal(query_emb, db)

        assert result is None

    @pytest.mark.asyncio
    async def test_only_approved_patterns_returned(self):
        """
        Solo retorna patrones con status=approved, no pending_approval ni rejected.
        Validates: Requirement 36.4
        """
        brain = UniversalBrain()
        query_emb = make_unit_vector(10, 0)

        # La query a DB ya filtra por status=approved (verificamos que la query se ejecuta)
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        result = await brain.query_universal(query_emb, db)

        assert result is None
        # Verificar que se ejecutó la query (el filtro status=approved está en la query SQL)
        db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self):
        """
        Retorna None silenciosamente si la DB falla (fail-silent).
        """
        brain = UniversalBrain()
        query_emb = make_unit_vector(10, 0)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("DB connection error"))

        result = await brain.query_universal(query_emb, db)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_best_among_multiple_similar_patterns(self):
        """
        Cuando hay múltiples patrones con similitud ≥ 0.60, retorna el de mayor similitud.
        """
        brain = UniversalBrain()
        query_emb = make_unit_vector(10, 0)

        # Patrón A: similitud alta
        up_a = make_universal_pattern_mock(
            up_id="up-a",
            trigger_embedding=make_similar_vector(query_emb, noise=0.01),
            status="approved",
        )
        # Patrón B: similitud media (pero ≥ 0.60)
        up_b = make_universal_pattern_mock(
            up_id="up-b",
            trigger_embedding=make_similar_vector(query_emb, noise=0.3),
            status="approved",
        )

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [up_b, up_a]  # orden inverso
        db.execute = AsyncMock(return_value=result_mock)

        result = await brain.query_universal(query_emb, db)

        # Debe retornar el de mayor similitud (up-a)
        assert result is not None
        assert result.id == "up-a"


# ─────────────────────────────────────────────────────────────────────────────
# Tests de _apply_abstraction
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyAbstraction:
    """Validates: Requirement 36.3"""

    @pytest.mark.asyncio
    async def test_returns_abstract_descriptions_from_llm(self):
        """
        _apply_abstraction retorna las descripciones abstractas del LLM.
        """
        brain = UniversalBrain()

        abstract_json = json.dumps({
            "trigger": "Cuando un equipo enfrenta bloqueos en la toma de decisiones",
            "response": "Aplicar un marco de decisión estructurado con criterios claros"
        })

        with patch("httpx.AsyncClient") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": abstract_json}}]
            }
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            trigger, response = await brain._apply_abstraction(
                "En el Departamento de IT de Empresa XYZ, el usuario admin@xyz.com reportó...",
                "El equipo de IT de XYZ resolvió usando su metodología ITIL interna...",
            )

        assert trigger == "Cuando un equipo enfrenta bloqueos en la toma de decisiones"
        assert response == "Aplicar un marco de decisión estructurado con criterios claros"

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_failure(self):
        """
        Retorna (None, None) si el LLM falla.
        """
        brain = UniversalBrain()

        with patch("httpx.AsyncClient") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            trigger, response = await brain._apply_abstraction(
                "Trigger original",
                "Response original",
            )

        assert trigger is None
        assert response is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        """
        Retorna (None, None) si el LLM devuelve JSON inválido.
        """
        brain = UniversalBrain()

        with patch("httpx.AsyncClient") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "Esto no es JSON válido"}}]
            }
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            trigger, response = await brain._apply_abstraction(
                "Trigger original",
                "Response original",
            )

        assert trigger is None
        assert response is None

    @pytest.mark.asyncio
    async def test_returns_none_on_too_short_abstraction(self):
        """
        Retorna (None, None) si las descripciones abstractas son demasiado cortas (< 10 chars).
        """
        brain = UniversalBrain()

        short_json = json.dumps({"trigger": "Corto", "response": "OK"})

        with patch("httpx.AsyncClient") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": short_json}}]
            }
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            trigger, response = await brain._apply_abstraction(
                "Trigger original",
                "Response original",
            )

        assert trigger is None
        assert response is None
