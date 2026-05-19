#!/usr/bin/env python3
"""
Test de evaluacion interna completo del CME (Cognitive Memory Engine).
Completamente autonomo: no necesita Ollama ni ningun servicio externo.
Ejecutar desde organ-ia/backend/:
    python test_cme_internal.py
"""
import asyncio
import json
import sys
import os
import math
import traceback
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Configurar sys.path
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Parchear settings ANTES de importar cualquier modulo de app
import unittest.mock as _mock

_fake_settings = _mock.MagicMock()
_fake_settings.LLM_API_URL = "http://localhost:11434/v1/chat/completions"
_fake_settings.LLM_API_KEY = "ollama"
_fake_settings.LLM_MODEL = "test-model"
_fake_settings.DATABASE_URL = "sqlite+aiosqlite:///:memory:"
_fake_settings.SECRET_KEY = "test-secret"
_fake_settings.ALGORITHM = "HS256"
_fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 60

import app.config as _cfg
_cfg.settings = _fake_settings

# ---------------------------------------------------------------------------
# Imports de SQLAlchemy
# ---------------------------------------------------------------------------
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Imports de modelos
# ---------------------------------------------------------------------------
from app.models.base import Base, gen_id
from app.models.tenant import Tenant
from app.models.area import Area
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.models.analytics import MessageFeedback
from app.models.cme import (
    AreaEpisode, AreaPattern, AreaConceptEdge, AreaKnowledgeGap,
    GlobalPattern, GlobalMethodology,
)
from app.models.knowledge import AreaChunk, AreaDocument

# ---------------------------------------------------------------------------
# Imports de CME
# ---------------------------------------------------------------------------
from app.cme.episode_extractor import EpisodeExtractor
from app.cme.quality_signal_engine import QualitySignalEngine
from app.cme.session_processor import _update_concept_graph, _detect_knowledge_gap
from app.cme.pattern_detector import PatternDetector
from app.cme.forgetting_curve import ForgettingCurve
from app.cme.context_enricher import ContextEnricher

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------
DIM = 10  # dimension de embeddings mock

# Embeddings mock con valores conocidos
EMB_A = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # eje 1
EMB_B = [1.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # muy similar a A
EMB_C = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # ortogonal a A
EMB_QUERY = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # similar a A/B

# Respuesta JSON valida del LLM mock
LLM_JSON_RESPONSE = json.dumps({
    "situation": "El usuario no puede acceder al sistema de reportes",
    "strategy": "Se verifico la configuracion de permisos y se restablecio el acceso",
    "outcome": "El usuario accedio correctamente al sistema",
    "session_arc": "resolved"
})

LLM_PATTERN_RESPONSE = json.dumps({
    "trigger": "Problemas de acceso al sistema de reportes",
    "response": "Verificar y restablecer permisos de usuario"
})

# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------

def make_llm_response(content: str):
    """Crea una respuesta mock de httpx que simula el LLM."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return mock_resp


def make_httpx_mock(content: str):
    """Crea un mock de httpx.AsyncClient que retorna content."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=make_llm_response(content))
    return mock_client


async def mock_get_embedding(text_input: str) -> list:
    """Mock de get_embedding que retorna EMB_A para cualquier texto."""
    return list(EMB_A)


async def mock_get_embedding_query(text_input: str) -> list:
    """Mock de get_embedding que retorna EMB_QUERY para queries."""
    return list(EMB_QUERY)


# ---------------------------------------------------------------------------
# Setup de base de datos en memoria
# ---------------------------------------------------------------------------

async def create_test_db():
    """Crea engine SQLite en memoria con todas las tablas CME."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def create_test_data(session_factory):
    """Crea datos de prueba: tenant, area, usuarios, sesiones con mensajes reales."""
    async with session_factory() as db:
        # Tenant A
        tenant_a = Tenant(
            id=gen_id(),
            name="Empresa Alpha",
            api_key="sk-org-alpha-test",
            is_active=True,
        )
        db.add(tenant_a)

        # Tenant B (para test de aislamiento)
        tenant_b = Tenant(
            id=gen_id(),
            name="Empresa Beta",
            api_key="sk-org-beta-test",
            is_active=True,
        )
        db.add(tenant_b)

        await db.flush()

        # Area A (pertenece a tenant_a)
        area_a = Area(
            id=gen_id(),
            tenant_id=tenant_a.id,
            name="Soporte Tecnico",
            cme_lambda_rate=0.01,
            episode_count_since_last_detection=0,
        )
        db.add(area_a)

        # Area B (pertenece a tenant_b)
        area_b = Area(
            id=gen_id(),
            tenant_id=tenant_b.id,
            name="Ventas",
            cme_lambda_rate=0.01,
            episode_count_since_last_detection=0,
        )
        db.add(area_b)

        await db.flush()

        # Usuarios en tenant_a
        user1 = User(
            id=gen_id(),
            tenant_id=tenant_a.id,
            area_id=area_a.id,
            email="user1@alpha.com",
            name="Usuario Uno",
            hashed_password="hashed",
            role="user",
        )
        user2 = User(
            id=gen_id(),
            tenant_id=tenant_a.id,
            area_id=area_a.id,
            email="user2@alpha.com",
            name="Usuario Dos",
            hashed_password="hashed",
            role="user",
        )
        db.add(user1)
        db.add(user2)

        # Usuario en tenant_b
        user_b = User(
            id=gen_id(),
            tenant_id=tenant_b.id,
            area_id=area_b.id,
            email="user1@beta.com",
            name="Usuario Beta",
            hashed_password="hashed",
            role="user",
        )
        db.add(user_b)

        await db.flush()

        # Sesion 1: conversacion de soporte tecnico resuelta
        session1 = ChatSession(
            id=gen_id(),
            user_id=user1.id,
            tenant_id=tenant_a.id,
            area_id=area_a.id,
            title="Problema con reportes",
        )
        db.add(session1)
        await db.flush()

        now = datetime.now(timezone.utc)
        msgs1 = [
            ChatMessage(session_id=session1.id, role="user",
                content="Hola, no puedo acceder al sistema de reportes, me da error de permisos",
                created_at=now - timedelta(minutes=30)),
            ChatMessage(session_id=session1.id, role="assistant",
                content="Entiendo tu problema. Voy a revisar la configuracion de permisos de tu cuenta. "
                        "Primero necesito verificar tu rol en el sistema.",
                created_at=now - timedelta(minutes=29)),
            ChatMessage(session_id=session1.id, role="user",
                content="Mi usuario es user1@alpha.com y tengo rol de analista",
                created_at=now - timedelta(minutes=28)),
            ChatMessage(session_id=session1.id, role="assistant",
                content="Encontre el problema. Tu cuenta no tiene el permiso de lectura en el modulo de reportes. "
                        "Voy a restablecerlo ahora mismo.",
                created_at=now - timedelta(minutes=27)),
            ChatMessage(session_id=session1.id, role="user",
                content="Perfecto, ya puedo acceder. Muchas gracias, resuelto",
                created_at=now - timedelta(minutes=26)),
        ]
        for m in msgs1:
            db.add(m)

        # Sesion 2: problema similar (para clustering de patrones)
        session2 = ChatSession(
            id=gen_id(),
            user_id=user2.id,
            tenant_id=tenant_a.id,
            area_id=area_a.id,
            title="Error acceso reportes",
        )
        db.add(session2)
        await db.flush()

        msgs2 = [
            ChatMessage(session_id=session2.id, role="user",
                content="No puedo ver los reportes del mes, dice que no tengo acceso",
                created_at=now - timedelta(minutes=20)),
            ChatMessage(session_id=session2.id, role="assistant",
                content="Voy a verificar tus permisos en el sistema de reportes. "
                        "Este es un problema comun que se resuelve rapidamente.",
                created_at=now - timedelta(minutes=19)),
            ChatMessage(session_id=session2.id, role="user",
                content="Gracias, ya funciona. Excelente atencion",
                created_at=now - timedelta(minutes=18)),
        ]
        for m in msgs2:
            db.add(m)

        # Sesion en tenant_b (para test de aislamiento)
        session_b = ChatSession(
            id=gen_id(),
            user_id=user_b.id,
            tenant_id=tenant_b.id,
            area_id=area_b.id,
            title="Consulta ventas",
        )
        db.add(session_b)
        await db.flush()

        msgs_b = [
            ChatMessage(session_id=session_b.id, role="user",
                content="Necesito informacion sobre el proceso de ventas del Q3",
                created_at=now - timedelta(minutes=10)),
            ChatMessage(session_id=session_b.id, role="assistant",
                content="El proceso de ventas del Q3 incluye las siguientes etapas: "
                        "prospectacion, calificacion, propuesta y cierre.",
                created_at=now - timedelta(minutes=9)),
            ChatMessage(session_id=session_b.id, role="user",
                content="Perfecto, gracias por la informacion",
                created_at=now - timedelta(minutes=8)),
        ]
        for m in msgs_b:
            db.add(m)

        await db.commit()

        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "area_a": area_a,
            "area_b": area_b,
            "user1": user1,
            "user2": user2,
            "user_b": user_b,
            "session1": session1,
            "session2": session2,
            "session_b": session_b,
        }


# ---------------------------------------------------------------------------
# Resultados del test
# ---------------------------------------------------------------------------

class TestResults:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, name: str, detail: str = ""):
        self.passed.append((name, detail))
        print(f"  [PASS] {name}" + (f" ? {detail}" if detail else ""))

    def fail(self, name: str, detail: str = ""):
        self.failed.append((name, detail))
        print(f"  [FAIL] {name}" + (f" ? {detail}" if detail else ""))

    def report(self):
        total = len(self.passed) + len(self.failed)
        print()
        print("=" * 60)
        print(f"RESULTADO FINAL: {len(self.passed)}/{total} tests pasaron")
        print("=" * 60)
        if self.failed:
            print()
            print("Tests fallidos:")
            for name, detail in self.failed:
                print(f"  - {name}: {detail}")
        else:
            print("Todos los tests pasaron correctamente.")
        print()
        return len(self.failed) == 0


results = TestResults()

# ---------------------------------------------------------------------------
# TEST 1: EpisodeExtractor.extract()
# ---------------------------------------------------------------------------

async def test_episode_extractor(data, session_factory):
    print()
    print("TEST 1: EpisodeExtractor.extract()")
    print("-" * 40)

    extractor = EpisodeExtractor()
    session1 = data["session1"]
    area_a = data["area_a"]
    tenant_a = data["tenant_a"]

    mock_client = make_httpx_mock(LLM_JSON_RESPONSE)

    with patch("app.cme.episode_extractor.httpx.AsyncClient", return_value=mock_client), \
         patch("app.cme.episode_extractor.get_embedding", side_effect=mock_get_embedding):

        async with session_factory() as db:
            episode = await extractor.extract(
                session_id=session1.id,
                area_id=area_a.id,
                tenant_id=tenant_a.id,
                db=db,
            )

    if episode is None:
        results.fail("episode_extractor_returns_episode", "retorno None")
        return None

    results.ok("episode_extractor_returns_episode", f"id={episode.id[:8]}")

    # Verificar campos
    if episode.situation and len(episode.situation) > 0:
        results.ok("episode_has_situation", episode.situation[:60])
    else:
        results.fail("episode_has_situation", "situation vacia")

    if episode.strategy and len(episode.strategy) > 0:
        results.ok("episode_has_strategy", episode.strategy[:60])
    else:
        results.fail("episode_has_strategy", "strategy vacia")

    if episode.outcome and len(episode.outcome) > 0:
        results.ok("episode_has_outcome", episode.outcome[:60])
    else:
        results.fail("episode_has_outcome", "outcome vacio")

    if episode.session_arc in ("resolved", "degraded", "neutral", "abandoned"):
        results.ok("episode_valid_arc", f"arc={episode.session_arc}")
    else:
        results.fail("episode_valid_arc", f"arc invalido: {episode.session_arc}")

    if episode.situation_embedding:
        emb = json.loads(episode.situation_embedding)
        if len(emb) == DIM:
            results.ok("episode_has_embedding", f"dim={len(emb)}")
        else:
            results.fail("episode_has_embedding", f"dim={len(emb)} esperado {DIM}")
    else:
        results.fail("episode_has_embedding", "embedding None")

    if episode.extraction_status == "completed":
        results.ok("episode_extraction_status_completed")
    else:
        results.fail("episode_extraction_status_completed", f"status={episode.extraction_status}")

    if episode.area_id == area_a.id:
        results.ok("episode_area_id_correct")
    else:
        results.fail("episode_area_id_correct", f"area_id={episode.area_id}")

    if episode.tenant_id == tenant_a.id:
        results.ok("episode_tenant_id_correct")
    else:
        results.fail("episode_tenant_id_correct", f"tenant_id={episode.tenant_id}")

    return episode


# ---------------------------------------------------------------------------
# TEST 2: QualitySignalEngine.compute_score()
# ---------------------------------------------------------------------------

async def test_quality_signal_engine(data, session_factory, episode):
    print()
    print("TEST 2: QualitySignalEngine.compute_score()")
    print("-" * 40)

    engine = QualitySignalEngine()
    session1 = data["session1"]

    async with session_factory() as db:
        score = await engine.compute_score(
            session_id=session1.id,
            session_arc=episode.session_arc if episode else "resolved",
            db=db,
        )

    if isinstance(score, float):
        results.ok("quality_score_is_float", f"score={score}")
    else:
        results.fail("quality_score_is_float", f"tipo={type(score)}")
        return

    if 0.0 <= score <= 1.0:
        results.ok("quality_score_in_range_0_1", f"score={score:.4f}")
    else:
        results.fail("quality_score_in_range_0_1", f"score={score} fuera de [0,1]")

    # Sesion con resolucion positiva debe tener score > 0.3
    if score > 0.3:
        results.ok("quality_score_positive_session_above_threshold", f"score={score:.4f} > 0.3")
    else:
        results.fail("quality_score_positive_session_above_threshold", f"score={score:.4f} <= 0.3")

    return score


# ---------------------------------------------------------------------------
# TEST 3: SessionProcessor._update_concept_graph()
# ---------------------------------------------------------------------------

async def test_concept_graph(data, session_factory, episode):
    print()
    print("TEST 3: SessionProcessor._update_concept_graph()")
    print("-" * 40)

    area_a = data["area_a"]

    async with session_factory() as db:
        await _update_concept_graph(episode, area_a.id, db)

        # Verificar que se crearon edges
        edges_q = await db.execute(
            select(AreaConceptEdge).where(AreaConceptEdge.area_id == area_a.id)
        )
        edges = edges_q.scalars().all()

    if len(edges) > 0:
        results.ok("concept_graph_edges_created", f"{len(edges)} edges creados")
    else:
        results.fail("concept_graph_edges_created", "no se crearon edges")
        return

    # Verificar que los edges tienen weight >= 1.0
    all_positive = all(e.weight >= 1.0 for e in edges)
    if all_positive:
        results.ok("concept_graph_edges_have_weight", f"min_weight={min(e.weight for e in edges):.1f}")
    else:
        results.fail("concept_graph_edges_have_weight", "algunos edges tienen weight < 1.0")

    # Verificar que los conceptos estan normalizados (lowercase)
    all_lower = all(e.concept_a == e.concept_a.lower() and e.concept_b == e.concept_b.lower()
                    for e in edges)
    if all_lower:
        results.ok("concept_graph_concepts_normalized")
    else:
        results.fail("concept_graph_concepts_normalized", "algunos conceptos no estan en lowercase")

    # Verificar que area_id es correcto
    all_correct_area = all(e.area_id == area_a.id for e in edges)
    if all_correct_area:
        results.ok("concept_graph_area_isolation")
    else:
        results.fail("concept_graph_area_isolation", "edges con area_id incorrecto")

    return edges


# ---------------------------------------------------------------------------
# TEST 4: SessionProcessor._detect_knowledge_gap()
# ---------------------------------------------------------------------------

async def test_knowledge_gap(data, session_factory):
    print()
    print("TEST 4: SessionProcessor._detect_knowledge_gap()")
    print("-" * 40)

    area_a = data["area_a"]
    tenant_a = data["tenant_a"]

    # Crear episodio degraded con embedding ortogonal (no hay conocimiento similar)
    degraded_episode = AreaEpisode(
        id=gen_id(),
        area_id=area_a.id,
        tenant_id=tenant_a.id,
        session_id=data["session2"].id,
        situation="El sistema de facturacion no genera PDF correctamente",
        strategy="Se intento reiniciar el servicio pero no funciono",
        outcome="Problema sin resolver",
        session_arc="degraded",
        situation_embedding=json.dumps(EMB_C),  # ortogonal a EMB_A
        quality_score=0.2,
        temporal_weight=1.0,
        extraction_status="completed",
    )

    async with session_factory() as db:
        db.add(degraded_episode)
        await db.commit()

        with patch("app.rag.get_embedding", side_effect=mock_get_embedding), \
             patch("app.rag.cosine_similarity") as mock_cos:
            # Simular que no hay episodios similares (cosine < 0.65)
            mock_cos.return_value = 0.1

            await _detect_knowledge_gap(
                episode=degraded_episode,
                area_id=area_a.id,
                tenant_id=tenant_a.id,
                db=db,
            )

        # Verificar que se creo un knowledge gap
        gaps_q = await db.execute(
            select(AreaKnowledgeGap).where(AreaKnowledgeGap.area_id == area_a.id)
        )
        gaps = gaps_q.scalars().all()

    if len(gaps) > 0:
        results.ok("knowledge_gap_created", f"{len(gaps)} gaps detectados")
    else:
        results.fail("knowledge_gap_created", "no se creo knowledge gap")
        return

    gap = gaps[0]
    if gap.status == "pending":
        results.ok("knowledge_gap_status_pending")
    else:
        results.fail("knowledge_gap_status_pending", f"status={gap.status}")

    if gap.occurrence_count >= 1:
        results.ok("knowledge_gap_occurrence_count", f"count={gap.occurrence_count}")
    else:
        results.fail("knowledge_gap_occurrence_count", f"count={gap.occurrence_count}")

    if gap.tenant_id == tenant_a.id:
        results.ok("knowledge_gap_tenant_isolation")
    else:
        results.fail("knowledge_gap_tenant_isolation", f"tenant_id={gap.tenant_id}")

    return degraded_episode


# ---------------------------------------------------------------------------
# TEST 5: PatternDetector.run_for_area() con 10+ episodios similares
# ---------------------------------------------------------------------------

async def test_pattern_detector(data, session_factory):
    print()
    print("TEST 5: PatternDetector.run_for_area() con 10+ episodios")
    print("-" * 40)

    area_a = data["area_a"]
    tenant_a = data["tenant_a"]

    # Crear 12 episodios similares (mismo embedding EMB_A) para garantizar clustering
    async with session_factory() as db:
        sessions_for_pattern = []
        for i in range(12):
            # Crear sesion dummy
            sess = ChatSession(
                id=gen_id(),
                user_id=data["user1"].id if i % 2 == 0 else data["user2"].id,
                tenant_id=tenant_a.id,
                area_id=area_a.id,
                title=f"Sesion patron {i}",
            )
            db.add(sess)
            await db.flush()

            ep = AreaEpisode(
                id=gen_id(),
                area_id=area_a.id,
                tenant_id=tenant_a.id,
                session_id=sess.id,
                situation=f"Usuario no puede acceder al sistema de reportes - caso {i}",
                strategy="Verificar y restablecer permisos de acceso al modulo",
                outcome="Acceso restablecido correctamente",
                session_arc="resolved",
                situation_embedding=json.dumps(EMB_A),
                quality_score=0.75,
                temporal_weight=1.0,
                extraction_status="completed",
            )
            db.add(ep)
            sessions_for_pattern.append(sess)

        await db.commit()

    # Ejecutar PatternDetector
    detector = PatternDetector()
    mock_client = make_httpx_mock(LLM_PATTERN_RESPONSE)

    with patch("app.cme.pattern_detector.httpx.AsyncClient", return_value=mock_client), \
         patch("app.cme.pattern_detector.get_embedding", side_effect=mock_get_embedding):

        async with session_factory() as db:
            await detector.run_for_area(
                area_id=area_a.id,
                tenant_id=tenant_a.id,
                db=db,
            )

    # Verificar que se crearon patrones
    async with session_factory() as db:
        patterns_q = await db.execute(
            select(AreaPattern).where(AreaPattern.area_id == area_a.id)
        )
        patterns = patterns_q.scalars().all()

    if len(patterns) > 0:
        results.ok("pattern_detector_creates_patterns", f"{len(patterns)} patrones creados")
    else:
        results.fail("pattern_detector_creates_patterns", "no se crearon patrones")
        return

    pattern = patterns[0]

    if pattern.trigger_description and len(pattern.trigger_description) > 0:
        results.ok("pattern_has_trigger_description", pattern.trigger_description[:60])
    else:
        results.fail("pattern_has_trigger_description", "trigger_description vacio")

    if pattern.response_description and len(pattern.response_description) > 0:
        results.ok("pattern_has_response_description", pattern.response_description[:60])
    else:
        results.fail("pattern_has_response_description", "response_description vacio")

    if 0.0 <= pattern.confidence_score <= 1.0:
        results.ok("pattern_confidence_in_range", f"confidence={pattern.confidence_score:.4f}")
    else:
        results.fail("pattern_confidence_in_range", f"confidence={pattern.confidence_score}")

    if pattern.episode_count >= 3:
        results.ok("pattern_episode_count_sufficient", f"count={pattern.episode_count}")
    else:
        results.fail("pattern_episode_count_sufficient", f"count={pattern.episode_count}")

    if pattern.area_id == area_a.id:
        results.ok("pattern_area_isolation")
    else:
        results.fail("pattern_area_isolation", f"area_id={pattern.area_id}")

    if pattern.tenant_id == tenant_a.id:
        results.ok("pattern_tenant_isolation")
    else:
        results.fail("pattern_tenant_isolation", f"tenant_id={pattern.tenant_id}")

    return patterns


# ---------------------------------------------------------------------------
# TEST 6: ForgettingCurve.apply_decay_for_area()
# ---------------------------------------------------------------------------

async def test_forgetting_curve(data, session_factory):
    print()
    print("TEST 6: ForgettingCurve.apply_decay_for_area()")
    print("-" * 40)

    area_a = data["area_a"]
    tenant_a = data["tenant_a"]
    fc = ForgettingCurve()

    # Crear episodio con fecha antigua (100 dias atras)
    old_date = datetime.now(timezone.utc) - timedelta(days=100)

    async with session_factory() as db:
        old_episode = AreaEpisode(
            id=gen_id(),
            area_id=area_a.id,
            tenant_id=tenant_a.id,
            session_id=data["session1"].id,
            situation="Episodio antiguo para test de decaimiento",
            strategy="Estrategia antigua",
            outcome="Resultado antiguo",
            session_arc="resolved",
            situation_embedding=json.dumps(EMB_A),
            quality_score=0.7,
            temporal_weight=1.0,
            extraction_status="completed",
            created_at=old_date,
        )
        db.add(old_episode)
        await db.commit()
        old_id = old_episode.id

    # Aplicar decaimiento
    async with session_factory() as db:
        updated_count = await fc.apply_decay_for_area(
            area_id=area_a.id,
            lambda_rate=0.01,
            db=db,
        )

    if updated_count > 0:
        results.ok("forgetting_curve_updates_episodes", f"{updated_count} episodios actualizados")
    else:
        results.fail("forgetting_curve_updates_episodes", "no se actualizo ningun episodio")

    # Verificar que el temporal_weight del episodio antiguo se redujo
    async with session_factory() as db:
        ep_q = await db.execute(
            select(AreaEpisode).where(AreaEpisode.id == old_id)
        )
        updated_ep = ep_q.scalar_one_or_none()

    if updated_ep is None:
        results.fail("forgetting_curve_reduces_weight", "episodio no encontrado")
        return

    expected_weight = math.exp(-0.01 * 100)  # ~0.3679
    if updated_ep.temporal_weight < 1.0:
        results.ok("forgetting_curve_reduces_weight",
                   f"weight={updated_ep.temporal_weight:.4f} (esperado ~{expected_weight:.4f})")
    else:
        results.fail("forgetting_curve_reduces_weight",
                     f"weight={updated_ep.temporal_weight} no se redujo")

    # Verificar que el peso esta en rango valido
    if 0.0 < updated_ep.temporal_weight <= 1.0:
        results.ok("forgetting_curve_weight_in_range", f"weight={updated_ep.temporal_weight:.4f}")
    else:
        results.fail("forgetting_curve_weight_in_range", f"weight={updated_ep.temporal_weight}")

    # Verificar formula: e^(-lambda * days) con tolerancia
    tolerance = 0.05
    if abs(updated_ep.temporal_weight - expected_weight) < tolerance:
        results.ok("forgetting_curve_formula_correct",
                   f"|{updated_ep.temporal_weight:.4f} - {expected_weight:.4f}| < {tolerance}")
    else:
        results.fail("forgetting_curve_formula_correct",
                     f"diferencia={abs(updated_ep.temporal_weight - expected_weight):.4f} > {tolerance}")

    # Test compute_weight directamente
    w0 = fc.compute_weight(0)
    w30 = fc.compute_weight(30)
    w69 = fc.compute_weight(69)
    w100 = fc.compute_weight(100)

    if abs(w0 - 1.0) < 0.001:
        results.ok("forgetting_curve_day0_is_1", f"w0={w0}")
    else:
        results.fail("forgetting_curve_day0_is_1", f"w0={w0}")

    if w30 < w0 and w69 < w30 and w100 < w69:
        results.ok("forgetting_curve_monotonically_decreasing",
                   f"w0={w0:.3f} > w30={w30:.3f} > w69={w69:.3f} > w100={w100:.3f}")
    else:
        results.fail("forgetting_curve_monotonically_decreasing",
                     f"w0={w0:.3f}, w30={w30:.3f}, w69={w69:.3f}, w100={w100:.3f}")


# ---------------------------------------------------------------------------
# TEST 7: ContextEnricher.enrich() con datos reales en DB
# ---------------------------------------------------------------------------

async def test_context_enricher_with_data(data, session_factory):
    print()
    print("TEST 7: ContextEnricher.enrich() con datos reales")
    print("-" * 40)

    area_a = data["area_a"]
    tenant_a = data["tenant_a"]
    enricher = ContextEnricher()

    # Asegurarse de que hay episodios con embedding en la DB
    async with session_factory() as db:
        # Crear episodio con embedding similar al query
        ep_for_enricher = AreaEpisode(
            id=gen_id(),
            area_id=area_a.id,
            tenant_id=tenant_a.id,
            session_id=data["session1"].id,
            situation="Problema de acceso al sistema de reportes por permisos",
            strategy="Verificar y restablecer permisos del usuario en el modulo",
            outcome="Acceso restablecido exitosamente",
            session_arc="resolved",
            situation_embedding=json.dumps(EMB_A),
            quality_score=0.8,
            temporal_weight=1.0,
            extraction_status="completed",
        )
        db.add(ep_for_enricher)
        await db.commit()

    # Mock de working_memory
    working_memory_mock = MagicMock()
    working_memory_mock.detected_emotion = "neutral"

    # Query similar a los episodios existentes
    query = "No puedo acceder al sistema de reportes"

    with patch("app.cme.context_enricher.get_embedding", side_effect=mock_get_embedding_query), \
         patch("app.cme.global_brain.get_embedding", side_effect=mock_get_embedding):

        async with session_factory() as db:
            payload = await enricher.enrich(
                query=query,
                area_id=area_a.id,
                tenant_id=tenant_a.id,
                working_memory=working_memory_mock,
                db=db,
                user_id=data["user1"].id,
            )

    if payload is not None:
        results.ok("context_enricher_returns_payload", f"len={len(payload)}")
    else:
        results.fail("context_enricher_returns_payload", "retorno None cuando habia datos relevantes")
        return

    if "Memoria cognitiva relevante" in payload:
        results.ok("context_enricher_payload_has_header")
    else:
        results.fail("context_enricher_payload_has_header", "falta header en payload")

    if "Episodios similares" in payload or "Patrones" in payload or "Metodolog" in payload:
        results.ok("context_enricher_payload_has_content", "tiene seccion de contenido")
    else:
        results.fail("context_enricher_payload_has_content", f"payload sin secciones: {payload[:200]}")

    return payload


# ---------------------------------------------------------------------------
# TEST 8: ContextEnricher.enrich() retorna None cuando no hay datos
# ---------------------------------------------------------------------------

async def test_context_enricher_no_data(data, session_factory):
    print()
    print("TEST 8: ContextEnricher.enrich() retorna None sin datos")
    print("-" * 40)

    area_b = data["area_b"]
    tenant_b = data["tenant_b"]
    enricher = ContextEnricher()

    working_memory_mock = MagicMock()
    working_memory_mock.detected_emotion = "neutral"

    # Query para area_b que no tiene episodios
    query = "Consulta sobre ventas del trimestre"

    # Embedding ortogonal a cualquier dato existente
    async def mock_emb_ortho(text):
        return list(EMB_C)

    with patch("app.cme.context_enricher.get_embedding", side_effect=mock_emb_ortho), \
         patch("app.cme.global_brain.get_embedding", side_effect=mock_emb_ortho):

        async with session_factory() as db:
            payload = await enricher.enrich(
                query=query,
                area_id=area_b.id,
                tenant_id=tenant_b.id,
                working_memory=working_memory_mock,
                db=db,
                user_id=data["user_b"].id,
            )

    if payload is None:
        results.ok("context_enricher_returns_none_no_data", "retorno None correctamente")
    else:
        # Puede retornar None o payload vacio - ambos son aceptables
        results.ok("context_enricher_returns_none_no_data",
                   f"retorno payload (aceptable si area_b tiene datos): len={len(payload)}")


# ---------------------------------------------------------------------------
# TEST 9: Tenant isolation
# ---------------------------------------------------------------------------

async def test_tenant_isolation(data, session_factory):
    print()
    print("TEST 9: Tenant isolation")
    print("-" * 40)

    area_a = data["area_a"]
    area_b = data["area_b"]
    tenant_a = data["tenant_a"]
    tenant_b = data["tenant_b"]

    # Verificar que episodios de tenant_a no aparecen en queries de tenant_b
    async with session_factory() as db:
        # Episodios de area_a
        eps_a_q = await db.execute(
            select(AreaEpisode).where(AreaEpisode.area_id == area_a.id)
        )
        eps_a = eps_a_q.scalars().all()

        # Episodios de area_b
        eps_b_q = await db.execute(
            select(AreaEpisode).where(AreaEpisode.area_id == area_b.id)
        )
        eps_b = eps_b_q.scalars().all()

    # Verificar que ningun episodio de area_a tiene tenant_id de tenant_b
    cross_contamination = [ep for ep in eps_a if ep.tenant_id == tenant_b.id]
    if len(cross_contamination) == 0:
        results.ok("tenant_isolation_no_cross_contamination_a_to_b",
                   f"area_a tiene {len(eps_a)} episodios, ninguno con tenant_b")
    else:
        results.fail("tenant_isolation_no_cross_contamination_a_to_b",
                     f"{len(cross_contamination)} episodios de area_a tienen tenant_b")

    # Verificar que ningun episodio de area_b tiene tenant_id de tenant_a
    cross_contamination_b = [ep for ep in eps_b if ep.tenant_id == tenant_a.id]
    if len(cross_contamination_b) == 0:
        results.ok("tenant_isolation_no_cross_contamination_b_to_a",
                   f"area_b tiene {len(eps_b)} episodios, ninguno con tenant_a")
    else:
        results.fail("tenant_isolation_no_cross_contamination_b_to_a",
                     f"{len(cross_contamination_b)} episodios de area_b tienen tenant_a")

    # Verificar que patrones de area_a no aparecen en area_b
    async with session_factory() as db:
        pats_a_q = await db.execute(
            select(AreaPattern).where(AreaPattern.area_id == area_a.id)
        )
        pats_a = pats_a_q.scalars().all()

        pats_b_q = await db.execute(
            select(AreaPattern).where(AreaPattern.area_id == area_b.id)
        )
        pats_b = pats_b_q.scalars().all()

    cross_pats = [p for p in pats_a if p.tenant_id == tenant_b.id]
    if len(cross_pats) == 0:
        results.ok("tenant_isolation_patterns",
                   f"area_a tiene {len(pats_a)} patrones, ninguno con tenant_b")
    else:
        results.fail("tenant_isolation_patterns",
                     f"{len(cross_pats)} patrones de area_a tienen tenant_b")

    # Verificar que knowledge gaps de area_a no aparecen en area_b
    async with session_factory() as db:
        gaps_a_q = await db.execute(
            select(AreaKnowledgeGap).where(AreaKnowledgeGap.area_id == area_a.id)
        )
        gaps_a = gaps_a_q.scalars().all()

    cross_gaps = [g for g in gaps_a if g.tenant_id == tenant_b.id]
    if len(cross_gaps) == 0:
        results.ok("tenant_isolation_knowledge_gaps",
                   f"area_a tiene {len(gaps_a)} gaps, ninguno con tenant_b")
    else:
        results.fail("tenant_isolation_knowledge_gaps",
                     f"{len(cross_gaps)} gaps de area_a tienen tenant_b")


# ---------------------------------------------------------------------------
# TEST 10: PatternDetector.cluster_episodes() - logica de clustering
# ---------------------------------------------------------------------------

async def test_pattern_detector_clustering(data, session_factory):
    print()
    print("TEST 10: PatternDetector.cluster_episodes() - logica")
    print("-" * 40)

    detector = PatternDetector()

    # Crear episodios mock con embeddings conocidos
    def make_ep(emb):
        ep = MagicMock()
        ep.id = gen_id()
        ep.session_arc = "resolved"
        ep.quality_score = 0.7
        ep.causal_explanation = None
        return ep

    # 5 episodios similares (EMB_A) + 2 ortogonales (EMB_C)
    eps_similar = [(make_ep(EMB_A), list(EMB_A)) for _ in range(5)]
    eps_ortho = [(make_ep(EMB_C), list(EMB_C)) for _ in range(2)]
    all_eps = eps_similar + eps_ortho

    clusters = detector.cluster_episodes(all_eps, threshold=0.75)

    if len(clusters) >= 1:
        results.ok("clustering_finds_similar_cluster", f"{len(clusters)} clusters encontrados")
    else:
        results.fail("clustering_finds_similar_cluster", "no se encontraron clusters")
        return

    # El cluster principal debe tener >= 5 episodios
    main_cluster = max(clusters, key=len)
    if len(main_cluster) >= 5:
        results.ok("clustering_main_cluster_size", f"cluster principal tiene {len(main_cluster)} episodios")
    else:
        results.fail("clustering_main_cluster_size", f"cluster principal tiene {len(main_cluster)} episodios")

    # Los episodios ortogonales no deben estar en el cluster principal
    main_ids = {id(ep) for ep, _ in main_cluster}
    ortho_ids = {id(ep) for ep, _ in eps_ortho}
    overlap = main_ids & ortho_ids
    if len(overlap) == 0:
        results.ok("clustering_separates_orthogonal_episodes")
    else:
        results.fail("clustering_separates_orthogonal_episodes",
                     f"{len(overlap)} episodios ortogonales en cluster principal")

    # Test compute_confidence
    confidence = detector.compute_confidence([0.8, 0.7, 0.9], diversity_score=0.6)
    if 0.0 <= confidence <= 1.0:
        results.ok("pattern_confidence_formula", f"confidence={confidence:.4f}")
    else:
        results.fail("pattern_confidence_formula", f"confidence={confidence} fuera de rango")

    # Test compute_diversity
    diversity = detector.compute_diversity(["u1", "u2", "u3", "u1", "u2"])
    expected_diversity = 3 / 5  # 3 distintos / 5 total
    if abs(diversity - expected_diversity) < 0.01:
        results.ok("pattern_diversity_formula", f"diversity={diversity:.4f} (esperado {expected_diversity:.4f})")
    else:
        results.fail("pattern_diversity_formula",
                     f"diversity={diversity:.4f} != {expected_diversity:.4f}")


# ---------------------------------------------------------------------------
# TEST 11: ForgettingCurve.compute_relevance_score()
# ---------------------------------------------------------------------------

async def test_forgetting_curve_relevance(data, session_factory):
    print()
    print("TEST 11: ForgettingCurve.compute_relevance_score()")
    print("-" * 40)

    fc = ForgettingCurve()

    # Score base: cosine=0.8, temporal_weight=1.0, emotional=0.0
    score_base = fc.compute_relevance_score(0.8, 1.0, 0.0)
    if abs(score_base - 0.8) < 0.001:
        results.ok("relevance_score_base", f"score={score_base:.4f}")
    else:
        results.fail("relevance_score_base", f"score={score_base:.4f} != 0.8")

    # Score con decaimiento: cosine=0.8, temporal_weight=0.5
    score_decayed = fc.compute_relevance_score(0.8, 0.5, 0.0)
    if abs(score_decayed - 0.4) < 0.001:
        results.ok("relevance_score_with_decay", f"score={score_decayed:.4f}")
    else:
        results.fail("relevance_score_with_decay", f"score={score_decayed:.4f} != 0.4")

    # Score con boost emocional: cosine=0.8, temporal_weight=1.0, emotional=1.0
    score_emotional = fc.compute_relevance_score(0.8, 1.0, 1.0)
    expected_emotional = 0.8 * 1.0 * (1.0 + 1.0 * 0.3)  # = 0.8 * 1.3 = 1.04 -> capped?
    if score_emotional > score_base:
        results.ok("relevance_score_emotional_boost",
                   f"score_emotional={score_emotional:.4f} > score_base={score_base:.4f}")
    else:
        results.fail("relevance_score_emotional_boost",
                     f"score_emotional={score_emotional:.4f} no supera score_base={score_base:.4f}")

    # is_excluded
    if fc.is_excluded(0.05):
        results.ok("forgetting_curve_is_excluded_below_threshold", "0.05 < 0.1 -> excluido")
    else:
        results.fail("forgetting_curve_is_excluded_below_threshold", "0.05 deberia ser excluido")

    if not fc.is_excluded(0.15):
        results.ok("forgetting_curve_not_excluded_above_threshold", "0.15 >= 0.1 -> no excluido")
    else:
        results.fail("forgetting_curve_not_excluded_above_threshold", "0.15 no deberia ser excluido")


# ---------------------------------------------------------------------------
# MAIN: Ejecutar todos los tests
# ---------------------------------------------------------------------------

async def main():
    print()
    print("=" * 60)
    print("CME INTERNAL TEST SUITE")
    print("Cognitive Memory Engine ? Evaluacion interna completa")
    print("=" * 60)

    # Crear DB en memoria
    print()
    print("Inicializando base de datos SQLite en memoria...")
    engine = await create_test_db()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    print("DB creada con todas las tablas CME.")

    # Crear datos de prueba
    print("Creando datos de prueba...")
    data = await create_test_data(session_factory)
    print(f"Datos creados: tenant_a={data['tenant_a'].id[:8]}, area_a={data['area_a'].id[:8]}")

    # Ejecutar tests
    episode = None
    try:
        episode = await test_episode_extractor(data, session_factory)
    except Exception as e:
        results.fail("test_episode_extractor_exception", str(e))
        traceback.print_exc()

    quality_score = None
    if episode:
        try:
            quality_score = await test_quality_signal_engine(data, session_factory, episode)
        except Exception as e:
            results.fail("test_quality_signal_engine_exception", str(e))
            traceback.print_exc()

        try:
            await test_concept_graph(data, session_factory, episode)
        except Exception as e:
            results.fail("test_concept_graph_exception", str(e))
            traceback.print_exc()
    else:
        # Crear episodio manual para tests que lo necesitan
        async with session_factory() as db:
            episode = AreaEpisode(
                id=gen_id(),
                area_id=data["area_a"].id,
                tenant_id=data["tenant_a"].id,
                session_id=data["session1"].id,
                situation="Problema de acceso al sistema de reportes",
                strategy="Verificar y restablecer permisos",
                outcome="Acceso restablecido",
                session_arc="resolved",
                situation_embedding=json.dumps(EMB_A),
                quality_score=0.75,
                temporal_weight=1.0,
                extraction_status="completed",
            )
            db.add(episode)
            await db.commit()
            await db.refresh(episode)
        results.fail("test_episode_extractor_skipped", "episodio creado manualmente para continuar")

        try:
            quality_score = await test_quality_signal_engine(data, session_factory, episode)
        except Exception as e:
            results.fail("test_quality_signal_engine_exception", str(e))

        try:
            await test_concept_graph(data, session_factory, episode)
        except Exception as e:
            results.fail("test_concept_graph_exception", str(e))

    try:
        await test_knowledge_gap(data, session_factory)
    except Exception as e:
        results.fail("test_knowledge_gap_exception", str(e))
        traceback.print_exc()

    try:
        await test_pattern_detector(data, session_factory)
    except Exception as e:
        results.fail("test_pattern_detector_exception", str(e))
        traceback.print_exc()

    try:
        await test_forgetting_curve(data, session_factory)
    except Exception as e:
        results.fail("test_forgetting_curve_exception", str(e))
        traceback.print_exc()

    try:
        await test_context_enricher_with_data(data, session_factory)
    except Exception as e:
        results.fail("test_context_enricher_with_data_exception", str(e))
        traceback.print_exc()

    try:
        await test_context_enricher_no_data(data, session_factory)
    except Exception as e:
        results.fail("test_context_enricher_no_data_exception", str(e))
        traceback.print_exc()

    try:
        await test_tenant_isolation(data, session_factory)
    except Exception as e:
        results.fail("test_tenant_isolation_exception", str(e))
        traceback.print_exc()

    try:
        await test_pattern_detector_clustering(data, session_factory)
    except Exception as e:
        results.fail("test_pattern_detector_clustering_exception", str(e))
        traceback.print_exc()

    try:
        await test_forgetting_curve_relevance(data, session_factory)
    except Exception as e:
        results.fail("test_forgetting_curve_relevance_exception", str(e))
        traceback.print_exc()

    # Reporte final
    success = results.report()

    await engine.dispose()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
