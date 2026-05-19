#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DEMO: CME Aprendiendo en Tiempo Real
=====================================
Script de demostracion visual del Cognitive Memory Engine de Organ.IA.
Completamente autonomo - sin Ollama ni servicios externos.

Uso: python demo_learning.py
"""

import asyncio
import json
import math
import random
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACION DE COLORES Y FORMATO
# ─────────────────────────────────────────────────────────────────────────────

try:
    import colorama
    colorama.init(autoreset=True)
    CYAN    = colorama.Fore.CYAN
    GREEN   = colorama.Fore.GREEN
    YELLOW  = colorama.Fore.YELLOW
    MAGENTA = colorama.Fore.MAGENTA
    RED     = colorama.Fore.RED
    BLUE    = colorama.Fore.BLUE
    WHITE   = colorama.Fore.WHITE
    BOLD    = colorama.Style.BRIGHT
    RESET   = colorama.Style.RESET_ALL
except ImportError:
    CYAN = GREEN = YELLOW = MAGENTA = RED = BLUE = WHITE = BOLD = RESET = ""

def line(char="─", width=57):
    return char * width

def header(title, char="═", width=57):
    print(f"\n{BOLD}{CYAN}{char * width}{RESET}")
    pad = (width - len(title) - 2) // 2
    print(f"{BOLD}{CYAN}{char * pad}  {title}  {char * (width - pad - len(title) - 2)}{RESET}")
    print(f"{BOLD}{CYAN}{char * width}{RESET}")

def section(title):
    print(f"\n{YELLOW}{line()}{RESET}")
    print(f"{BOLD}{WHITE}{title}{RESET}")
    print(f"{YELLOW}{line()}{RESET}")

def pause(seconds=0.4):
    time.sleep(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# MOCKS DE EMBEDDINGS Y LLM
# ─────────────────────────────────────────────────────────────────────────────

# Embeddings de 8 dimensiones para que el clustering funcione correctamente.
# Los embeddings de "acceso a reportes" son muy similares entre si (cosine > 0.75)
# para que el PatternDetector los agrupe en el mismo cluster.

BASE_ACCESS_EMBEDDING = [0.82, 0.15, 0.45, 0.12, 0.67, 0.23, 0.55, 0.31]

def make_similar_embedding(base, noise=0.05):
    """Genera un embedding similar al base con pequeno ruido."""
    emb = [v + random.uniform(-noise, noise) for v in base]
    # Normalizar a longitud 1
    norm = math.sqrt(sum(v*v for v in emb))
    return [v / norm for v in emb]

def cosine_similarity_local(a, b):
    """Calcula cosine similarity entre dos vectores."""
    dot = sum(x*y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x*x for x in a))
    norm_b = math.sqrt(sum(y*y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

# Respuestas del LLM mock para extraccion de episodios
LLM_EPISODE_RESPONSES = [
    {
        "situation": "Usuario no puede acceder al sistema de reportes",
        "strategy": "Verificar permisos del usuario en el panel de administracion y resetear acceso",
        "outcome": "Acceso restaurado correctamente",
        "session_arc": "resolved"
    },
    {
        "situation": "Sistema de reportes no permite entrada al usuario",
        "strategy": "Revisar configuracion de roles y permisos, actualizar grupo de acceso",
        "outcome": "Problema de permisos resuelto",
        "session_arc": "resolved"
    },
    {
        "situation": "Usuario sin acceso a reportes del mes",
        "strategy": "Verificar permisos en Active Directory y sincronizar con sistema de reportes",
        "outcome": "Acceso habilitado tras sincronizacion",
        "session_arc": "resolved"
    },
]

# Respuestas del LLM mock para generacion de patrones
LLM_PATTERN_RESPONSE = json.dumps({
    "trigger": "problemas de acceso a reportes",
    "response": "verificar permisos del usuario y resetear acceso desde panel de administracion"
})


# ─────────────────────────────────────────────────────────────────────────────
# SETUP DE BASE DE DATOS EN MEMORIA
# ─────────────────────────────────────────────────────────────────────────────

async def setup_database():
    """Crea la DB SQLite en memoria con todas las tablas CME."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.models.base import Base

    # Importar todos los modelos para que Base los registre
    import app.models.tenant
    import app.models.user
    import app.models.area
    import app.models.chat
    import app.models.cme

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionFactory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, SessionFactory


async def create_user_and_area(db):
    """Crea tenant, usuario Marcelo y su area personal."""
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.area import Area
    from app.models.base import gen_id
    import hashlib

    tenant_id = gen_id()
    user_id = gen_id()
    area_id = gen_id()

    tenant = Tenant(
        id=tenant_id,
        name="Organizacion Demo",
        licenses_total=10,
        is_active=True,
        subscription_status="active",
    )
    db.add(tenant)

    user = User(
        id=user_id,
        tenant_id=tenant_id,
        area_id=area_id,
        email="marcelo@demo.org",
        name="Marcelo",
        hashed_password=hashlib.sha256(b"demo").hexdigest(),
        role="user",
        is_active=True,
    )
    db.add(user)

    area = Area(
        id=area_id,
        tenant_id=tenant_id,
        name="Mi Cerebro",
        cme_lambda_rate=0.01,
        episode_count_since_last_detection=0,
    )
    db.add(area)

    await db.commit()
    return tenant_id, user_id, area_id


async def create_chat_session(db, user_id, tenant_id, area_id, title="Conversacion"):
    """Crea una sesion de chat."""
    from app.models.chat import ChatSession
    from app.models.base import gen_id

    session = ChatSession(
        id=gen_id(),
        user_id=user_id,
        tenant_id=tenant_id,
        area_id=area_id,
        title=title,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def add_messages(db, session_id, messages):
    """Agrega mensajes a una sesion."""
    from app.models.chat import ChatMessage
    from app.models.base import gen_id

    now = datetime.now(timezone.utc)
    for i, (role, content) in enumerate(messages):
        msg = ChatMessage(
            id=gen_id(),
            session_id=session_id,
            role=role,
            content=content,
            created_at=now + timedelta(seconds=i * 30),
        )
        db.add(msg)
    await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONES DE EXTRACCION Y PROCESAMIENTO (CON MOCKS)
# ─────────────────────────────────────────────────────────────────────────────

async def extract_episode_mock(session_id, area_id, tenant_id, db, episode_data, embedding):
    """
    Extrae un episodio directamente (sin llamar al LLM real).
    Simula lo que hace EpisodeExtractor.extract() pero con datos predefinidos.
    """
    from app.models.cme import AreaEpisode
    from app.models.base import gen_id

    episode = AreaEpisode(
        id=gen_id(),
        area_id=area_id,
        tenant_id=tenant_id,
        session_id=session_id,
        situation=episode_data["situation"],
        strategy=episode_data["strategy"],
        outcome=episode_data["outcome"],
        session_arc=episode_data["session_arc"],
        situation_embedding=json.dumps(embedding),
        quality_score=None,
        temporal_weight=1.0,
        causal_explanation="Los permisos del sistema se dessincronizan periodicamente",
        emotional_intensity=0.1,
        extraction_status="completed",
    )
    db.add(episode)
    await db.commit()
    await db.refresh(episode)
    return episode


async def compute_quality_mock(session_arc):
    """Calcula un quality_score simulado basado en el arc."""
    base_scores = {
        "resolved": 0.75,
        "neutral": 0.55,
        "degraded": 0.30,
        "abandoned": 0.15,
    }
    base = base_scores.get(session_arc, 0.5)
    # Agregar pequena variacion para que los scores sean realistas
    return round(base + random.uniform(-0.05, 0.10), 2)


async def update_concept_graph(episode, area_id, db):
    """Actualiza el grafo de conceptos del area."""
    from app.models.cme import AreaConceptEdge
    from sqlalchemy import select
    import re

    text = f"{episode.situation} {episode.strategy}"
    text_lower = re.sub(r'[^\w\s]', '', text.lower())
    words = [w for w in text_lower.split() if len(w) > 4]

    concepts = set()
    for word in words:
        if len(word) > 5:
            concepts.add(word)
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        if len(bigram) > 8:
            concepts.add(bigram)

    concepts = list(concepts)[:10]
    now = datetime.now(timezone.utc)
    edge_count = 0

    for i in range(len(concepts)):
        for j in range(i + 1, min(i + 4, len(concepts))):
            concept_a = concepts[i]
            concept_b = concepts[j]

            from sqlalchemy import or_, and_
            edge_q = await db.execute(
                select(AreaConceptEdge).where(
                    AreaConceptEdge.area_id == area_id,
                    or_(
                        and_(AreaConceptEdge.concept_a == concept_a, AreaConceptEdge.concept_b == concept_b),
                        and_(AreaConceptEdge.concept_a == concept_b, AreaConceptEdge.concept_b == concept_a),
                    )
                )
            )
            edge = edge_q.scalar_one_or_none()

            if edge:
                edge.weight += 1.0
                edge.last_reinforced_at = now
            else:
                from app.models.base import gen_id
                edge = AreaConceptEdge(
                    id=gen_id(),
                    area_id=area_id,
                    concept_a=concept_a,
                    concept_b=concept_b,
                    weight=1.0,
                    last_reinforced_at=now,
                )
                db.add(edge)
                edge_count += 1

    await db.commit()
    return edge_count


async def run_pattern_detection_mock(area_id, tenant_id, db, episodes_with_emb):
    """
    Ejecuta deteccion de patrones directamente (sin llamar al LLM real).
    Simula PatternDetector.run_for_area() con datos predefinidos.
    """
    from app.models.cme import AreaPattern
    from app.models.base import gen_id
    from sqlalchemy import select

    if len(episodes_with_emb) < 3:
        return None

    # Calcular embedding promedio del cluster
    dim = len(episodes_with_emb[0][1])
    avg_emb = [0.0] * dim
    for _, emb in episodes_with_emb:
        for i, v in enumerate(emb):
            avg_emb[i] += v
    n = len(episodes_with_emb)
    avg_emb = [v / n for v in avg_emb]

    # Calcular metricas
    quality_scores = [ep.quality_score for ep, _ in episodes_with_emb if ep.quality_score]
    mean_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.5
    diversity_score = 0.6  # simulado: varios usuarios distintos
    confidence = round((mean_quality * 0.6) + (diversity_score * 0.4), 4)

    # Buscar patron existente
    pats_q = await db.execute(
        select(AreaPattern).where(AreaPattern.area_id == area_id)
    )
    existing = pats_q.scalars().all()

    for pat in existing:
        if pat.trigger_embedding:
            pat_emb = json.loads(pat.trigger_embedding)
            sim = cosine_similarity_local(avg_emb, pat_emb)
            if sim >= 0.75:
                # Actualizar patron existente
                pat.confidence_score = confidence
                pat.episode_count = len(episodes_with_emb)
                pat.diversity_score = diversity_score
                await db.commit()
                return pat

    # Crear nuevo patron
    pattern = AreaPattern(
        id=gen_id(),
        area_id=area_id,
        tenant_id=tenant_id,
        trigger_description="problemas de acceso a reportes",
        trigger_embedding=json.dumps(avg_emb),
        response_description="verificar permisos del usuario y resetear acceso desde panel de administracion",
        response_embedding=json.dumps(make_similar_embedding([0.3, 0.8, 0.2, 0.6, 0.1, 0.7, 0.4, 0.5])),
        causal_mechanism="Los permisos se dessincronizan cuando el usuario cambia de grupo o rol",
        confidence_score=confidence,
        diversity_score=diversity_score,
        episode_count=len(episodes_with_emb),
        distinct_user_count=1,
        abstraction_level=1,
        is_approved=True,  # Auto-aprobado para la demo
        is_failure_pattern=False,
        source_episode_ids=json.dumps([ep.id for ep, _ in episodes_with_emb]),
    )
    db.add(pattern)
    await db.commit()
    await db.refresh(pattern)
    return pattern


async def get_context_enricher_payload(query, area_id, tenant_id, db, query_embedding):
    """
    Obtiene el payload del ContextEnricher para una query.
    Simula lo que ve el LLM antes de responder.
    """
    from app.models.cme import AreaEpisode, AreaPattern
    from sqlalchemy import select

    sections = []

    # Buscar patrones relevantes
    pats_q = await db.execute(
        select(AreaPattern).where(
            AreaPattern.area_id == area_id,
            AreaPattern.is_approved == True,
        )
    )
    patterns = pats_q.scalars().all()

    relevant_patterns = []
    for p in patterns:
        if p.trigger_embedding:
            emb = json.loads(p.trigger_embedding)
            sim = cosine_similarity_local(query_embedding, emb)
            if sim >= 0.65:
                relevant_patterns.append((p, sim))

    relevant_patterns.sort(key=lambda x: x[1], reverse=True)

    if relevant_patterns:
        lines = []
        for p, sim in relevant_patterns[:2]:
            line_text = f"- Cuando hay {p.trigger_description}, la respuesta efectiva es {p.response_description} (confianza: {p.confidence_score:.2f})"
            if p.causal_mechanism:
                line_text += f"\n  (esto funciona porque {p.causal_mechanism[:80]})"
            lines.append(line_text)
        sections.append("### Patrones del area\n" + "\n".join(lines))

    # Buscar episodios relevantes
    eps_q = await db.execute(
        select(AreaEpisode).where(
            AreaEpisode.area_id == area_id,
            AreaEpisode.extraction_status == "completed",
        )
    )
    episodes = eps_q.scalars().all()

    relevant_episodes = []
    for ep in episodes:
        if ep.situation_embedding:
            emb = json.loads(ep.situation_embedding)
            cosine = cosine_similarity_local(query_embedding, emb)
            relevance = cosine * ep.temporal_weight
            if relevance >= 0.60:
                relevant_episodes.append((ep, relevance))

    relevant_episodes.sort(key=lambda x: x[1], reverse=True)

    if relevant_episodes:
        lines = []
        for ep, score in relevant_episodes[:3]:
            now = datetime.now(timezone.utc)
            created = ep.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days = (now - created).days
            days_str = f", hace {days} dias" if days > 0 else ", hoy"
            if ep.session_arc == "resolved":
                lines.append(f"- Resolvimos algo similar antes: {ep.strategy[:120]} (calidad: {ep.quality_score or 'N/A'}{days_str})")
        if lines:
            sections.append("### Episodios similares\n" + "\n".join(lines))

    if not sections:
        return None

    return "## Memoria cognitiva relevante\n\n" + "\n\n".join(sections)


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONES DE VISUALIZACION
# ─────────────────────────────────────────────────────────────────────────────

def show_context_payload(payload, label="Contexto inyectado al LLM"):
    """Muestra el payload del ContextEnricher de forma visual."""
    if payload is None:
        print(f"   {CYAN}[NINGUNO - primera vez]{RESET}")
        return

    print(f"   {BOLD}{BLUE}{label}:{RESET}")
    for line_text in payload.split("\n"):
        if line_text.startswith("##"):
            print(f"   {BOLD}{MAGENTA}{line_text}{RESET}")
        elif line_text.startswith("###"):
            print(f"   {CYAN}{line_text}{RESET}")
        elif line_text.startswith("- "):
            print(f"   {GREEN}{line_text}{RESET}")
        elif line_text.strip():
            print(f"   {WHITE}{line_text}{RESET}")


def show_episode(episode, episode_num):
    """Muestra un episodio extraido."""
    arc_icons = {"resolved": "✅", "degraded": "⚠️", "neutral": "➡️", "abandoned": "❌"}
    icon = arc_icons.get(episode.session_arc, "📝")
    print(f"   {icon} Episodio #{episode_num}: {BOLD}\"{episode.situation[:60]}...\"")
    print(f"      Estrategia: {episode.strategy[:80]}")
    print(f"      Arc: {CYAN}{episode.session_arc}{RESET} | Quality: {GREEN}{episode.quality_score}{RESET}")


def show_pattern(pattern):
    """Muestra un patron detectado."""
    print(f"   {BOLD}{MAGENTA}Patron detectado:{RESET}")
    print(f"   Trigger: {CYAN}\"{pattern.trigger_description}\"{RESET}")
    print(f"   Respuesta: {GREEN}\"{pattern.response_description[:80]}\"{RESET}")
    print(f"   Confianza: {YELLOW}{pattern.confidence_score:.2f}{RESET} | Episodios: {pattern.episode_count}")
    if pattern.causal_mechanism:
        print(f"   Mecanismo: {WHITE}\"{pattern.causal_mechanism[:80]}\"{RESET}")


async def count_concept_edges(area_id, db):
    """Cuenta las aristas del grafo de conceptos."""
    from app.models.cme import AreaConceptEdge
    from sqlalchemy import select, func

    result = await db.execute(
        select(func.count()).where(AreaConceptEdge.area_id == area_id)
    )
    return result.scalar() or 0


async def count_episodes(area_id, db):
    """Cuenta los episodios del area."""
    from app.models.cme import AreaEpisode
    from sqlalchemy import select, func

    result = await db.execute(
        select(func.count()).where(
            AreaEpisode.area_id == area_id,
            AreaEpisode.extraction_status == "completed"
        )
    )
    return result.scalar() or 0


async def count_patterns(area_id, db):
    """Cuenta los patrones del area."""
    from app.models.cme import AreaPattern
    from sqlalchemy import select, func

    result = await db.execute(
        select(func.count()).where(AreaPattern.area_id == area_id)
    )
    return result.scalar() or 0


async def get_avg_quality(area_id, db):
    """Calcula el quality score promedio del area."""
    from app.models.cme import AreaEpisode
    from sqlalchemy import select

    eps_q = await db.execute(
        select(AreaEpisode).where(
            AreaEpisode.area_id == area_id,
            AreaEpisode.quality_score.isnot(None)
        )
    )
    episodes = eps_q.scalars().all()
    if not episodes:
        return 0.0
    return round(sum(ep.quality_score for ep in episodes) / len(episodes), 2)


# ─────────────────────────────────────────────────────────────────────────────
# SIMULACION DE CONVERSACIONES
# ─────────────────────────────────────────────────────────────────────────────

CONVERSATIONS = [
    # Ronda 1: Primera conversacion
    {
        "user_query": "No puedo acceder al sistema de reportes",
        "messages": [
            ("user", "No puedo acceder al sistema de reportes"),
            ("assistant", "Entiendo. Voy a revisar tu acceso. Primero necesito verificar tus permisos en el sistema."),
            ("user", "Intente varias veces y no me deja entrar"),
            ("assistant", "He verificado tu cuenta. El problema es que tu usuario no tiene el permiso 'reportes_viewer'. Voy a agregarlo ahora."),
            ("user", "Perfecto, ya puedo entrar. Gracias!"),
            ("assistant", "Excelente! El acceso ha sido restaurado. Si vuelve a ocurrir, el problema suele ser una desincronizacion de permisos."),
        ],
        "episode_data": LLM_EPISODE_RESPONSES[0],
        "quality": 0.75,
    },
    # Ronda 2: Despues de 5 conversaciones similares (simulamos 4 adicionales)
    {
        "user_query": "El sistema de reportes no me deja entrar",
        "messages": [
            ("user", "El sistema de reportes no me deja entrar"),
            ("assistant", "Entiendo. Voy a revisar la configuracion de roles y permisos de tu cuenta."),
            ("user", "Ya intente cerrar sesion y volver a entrar"),
            ("assistant", "El problema es la configuracion de tu grupo de acceso. Voy a actualizar tu rol en el sistema."),
            ("user", "Listo, ya funciona. Gracias!"),
            ("assistant", "Perfecto! Problema de permisos resuelto. Recuerda que esto puede ocurrir cuando cambias de departamento."),
        ],
        "episode_data": LLM_EPISODE_RESPONSES[1],
        "quality": 0.82,
    },
    # Ronda 3: Despues de 12 conversaciones (patron consolidado)
    {
        "user_query": "No tengo acceso a los reportes del mes",
        "messages": [
            ("user", "No tengo acceso a los reportes del mes"),
            ("assistant", "Basandome en el historial del area, este tipo de problema generalmente se resuelve verificando los permisos. Voy a revisarlos ahora."),
            ("user", "Si, es el mismo problema de siempre"),
            ("assistant", "Exacto. He sincronizado tu acceso con Active Directory. Ya deberias poder ver los reportes."),
            ("user", "Perfecto, ya tengo acceso. Gracias!"),
            ("assistant", "Excelente! Acceso habilitado. El sistema aprende de cada caso para resolver esto mas rapido."),
        ],
        "episode_data": LLM_EPISODE_RESPONSES[2],
        "quality": 0.88,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# DEMO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

async def run_demo():
    """Ejecuta la demo completa del CME aprendiendo en tiempo real."""

    # Configurar seed para reproducibilidad
    random.seed(42)

    # ─── TITULO ───────────────────────────────────────────────────────────────
    header("DEMO: CME APRENDIENDO EN TIEMPO REAL")
    print(f"\n{BOLD}Este script demuestra como el sistema aprende de conversaciones reales.{RESET}")
    print(f"Cada ronda muestra como el contexto inyectado al LLM mejora con el tiempo.\n")
    pause(0.5)

    # ─── SETUP ────────────────────────────────────────────────────────────────
    print(f"{YELLOW}Inicializando base de datos en memoria...{RESET}", end=" ", flush=True)
    engine, SessionFactory = await setup_database()
    print(f"{GREEN}OK{RESET}")

    async with SessionFactory() as db:
        print(f"{YELLOW}Creando usuario Marcelo y area 'Mi Cerebro'...{RESET}", end=" ", flush=True)
        tenant_id, user_id, area_id = await create_user_and_area(db)
        print(f"{GREEN}OK{RESET}")

    print(f"\n{BOLD}{CYAN}Usuario: Marcelo | Area: Mi Cerebro{RESET}")
    print(f"{WHITE}Tenant ID: {tenant_id[:8]}... | Area ID: {area_id[:8]}...{RESET}")
    pause(0.8)

    # ─── ESTADO INICIAL ───────────────────────────────────────────────────────
    all_episodes_with_emb = []  # Acumulamos todos los episodios para el clustering
    episode_counter = 0

    # ─── RONDA 1 ──────────────────────────────────────────────────────────────
    section("RONDA 1: Primera conversacion (el sistema no sabe nada)")
    pause(0.3)

    conv = CONVERSATIONS[0]
    query_emb = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.03)

    print(f"\n{BOLD}Marcelo pregunta:{RESET}")
    q = conv["user_query"]
    print(f"   {CYAN}{q}{RESET}")
    pause(0.4)

    print(f"\n{BOLD}Contexto inyectado al LLM ANTES de responder:{RESET}")
    async with SessionFactory() as db:
        payload_before = await get_context_enricher_payload(
            conv["user_query"], area_id, tenant_id, db, query_emb
        )
    show_context_payload(payload_before)
    pause(0.5)

    print(f"\n{YELLOW}[Conversacion en curso...]{RESET}")
    for role, content in conv["messages"]:
        icon = "💬" if role == "user" else "🤖"
        name = "Marcelo" if role == "user" else "Bot"
        print(f"   {icon} {BOLD}{name}:{RESET} {content[:80]}")
        pause(0.15)

    print(f"\n{GREEN}✅ Sesion terminada{RESET}")
    pause(0.3)

    # Extraer episodio
    print(f"\n{YELLOW}📚 Extrayendo episodio de la sesion...{RESET}")
    episode_emb = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.04)

    async with SessionFactory() as db:
        session = await create_chat_session(db, user_id, tenant_id, area_id, "Acceso reportes 1")
        await add_messages(db, session.id, conv["messages"])
        episode = await extract_episode_mock(
            session.id, area_id, tenant_id, db, conv["episode_data"], episode_emb
        )
        episode.quality_score = conv["quality"]
        await db.commit()
        await update_concept_graph(episode, area_id, db)
        episode_counter += 1
        all_episodes_with_emb.append((episode, episode_emb))

        n_episodes = await count_episodes(area_id, db)
        n_patterns = await count_patterns(area_id, db)
        n_edges = await count_concept_edges(area_id, db)

    show_episode(episode, episode_counter)
    print(f"\n{BOLD}Estado del sistema despues de la Ronda 1:{RESET}")
    print(f"   {CYAN}Episodios acumulados: {n_episodes}{RESET}")
    print(f"   {YELLOW}Patrones detectados: {n_patterns} (necesita al menos 3 episodios similares){RESET}")
    print(f"   {WHITE}Concept graph: {n_edges} conexiones{RESET}")
    pause(0.5)


    # ─── SIMULAR 4 CONVERSACIONES ADICIONALES (para llegar a 5 antes de ronda 2) ──
    print(f"\n{YELLOW}[Simulando 4 conversaciones adicionales similares...]{RESET}")
    pause(0.3)

    extra_queries = [
        "No puedo ver los reportes de ventas",
        "El sistema de reportes me da error de acceso",
        "No tengo permiso para ver los reportes",
        "Los reportes no cargan, dice que no tengo acceso",
    ]
    extra_episodes_data = [
        {"situation": "Usuario sin acceso a reportes de ventas", "strategy": "Verificar y actualizar permisos de reportes", "outcome": "Acceso restaurado", "session_arc": "resolved"},
        {"situation": "Error de acceso en sistema de reportes", "strategy": "Resetear permisos y sincronizar grupos", "outcome": "Error resuelto", "session_arc": "resolved"},
        {"situation": "Usuario sin permiso para ver reportes", "strategy": "Agregar usuario al grupo de acceso de reportes", "outcome": "Permisos actualizados", "session_arc": "resolved"},
        {"situation": "Reportes no cargan por falta de acceso", "strategy": "Verificar permisos y limpiar cache de sesion", "outcome": "Reportes accesibles", "session_arc": "resolved"},
    ]

    async with SessionFactory() as db:
        for i, (q, ep_data) in enumerate(zip(extra_queries, extra_episodes_data)):
            emb = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.06)
            sess = await create_chat_session(db, user_id, tenant_id, area_id, f"Extra {i+1}")
            msgs = [
                ("user", q),
                ("assistant", "Voy a revisar tus permisos."),
                ("user", "Gracias, ya funciona!"),
                ("assistant", "Perfecto, acceso restaurado."),
            ]
            await add_messages(db, sess.id, msgs)
            ep = await extract_episode_mock(sess.id, area_id, tenant_id, db, ep_data, emb)
            ep.quality_score = round(0.72 + random.uniform(0, 0.10), 2)
            await db.commit()
            await update_concept_graph(ep, area_id, db)
            episode_counter += 1
            all_episodes_with_emb.append((ep, emb))
            print(f"   {GREEN}✓{RESET} Episodio #{episode_counter}: \"{q[:50]}\"")
            pause(0.1)

    pause(0.3)

    # ─── RONDA 2 ──────────────────────────────────────────────────────────────
    section("RONDA 2: Despues de 5 conversaciones similares")
    pause(0.3)

    conv = CONVERSATIONS[1]
    query_emb_2 = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.03)

    print(f"\n{BOLD}Marcelo pregunta:{RESET}")
    q = conv["user_query"]
    print(f"   {CYAN}{q}{RESET}")
    pause(0.4)

    # Mostrar contexto ANTES (con episodios pero sin patron aun)
    print(f"\n{BOLD}Contexto inyectado al LLM ANTES de responder:{RESET}")
    async with SessionFactory() as db:
        payload_r2_before = await get_context_enricher_payload(
            conv["user_query"], area_id, tenant_id, db, query_emb_2
        )
    show_context_payload(payload_r2_before)
    pause(0.5)

    print(f"\n{YELLOW}[Conversacion en curso...]{RESET}")
    for role, content in conv["messages"]:
        icon = "💬" if role == "user" else "🤖"
        name = "Marcelo" if role == "user" else "Bot"
        print(f"   {icon} {BOLD}{name}:{RESET} {content[:80]}")
        pause(0.15)

    print(f"\n{GREEN}✅ Sesion terminada{RESET}")
    pause(0.3)

    # Extraer episodio de ronda 2
    print(f"\n{YELLOW}📚 Extrayendo episodio...{RESET}")
    episode_emb_2 = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.04)

    async with SessionFactory() as db:
        session2 = await create_chat_session(db, user_id, tenant_id, area_id, "Acceso reportes 2")
        await add_messages(db, session2.id, conv["messages"])
        episode2 = await extract_episode_mock(
            session2.id, area_id, tenant_id, db, conv["episode_data"], episode_emb_2
        )
        episode2.quality_score = conv["quality"]
        await db.commit()
        await update_concept_graph(episode2, area_id, db)
        episode_counter += 1
        all_episodes_with_emb.append((episode2, episode_emb_2))

    show_episode(episode2, episode_counter)
    pause(0.3)

    # Ejecutar deteccion de patrones (tenemos 6 episodios similares)
    print(f"\n{YELLOW}🔍 Ejecutando deteccion de patrones (6 episodios acumulados)...{RESET}")
    pause(0.5)

    async with SessionFactory() as db:
        # Recargar episodios con sus embeddings actualizados
        from app.models.cme import AreaEpisode
        from sqlalchemy import select
        eps_q = await db.execute(
            select(AreaEpisode).where(
                AreaEpisode.area_id == area_id,
                AreaEpisode.extraction_status == "completed"
            )
        )
        db_episodes = eps_q.scalars().all()
        eps_with_emb = [(ep, json.loads(ep.situation_embedding)) for ep in db_episodes if ep.situation_embedding]

        pattern = await run_pattern_detection_mock(area_id, tenant_id, db, eps_with_emb)

        n_episodes = await count_episodes(area_id, db)
        n_patterns = await count_patterns(area_id, db)
        n_edges = await count_concept_edges(area_id, db)

    if pattern:
        show_pattern(pattern)

    print(f"\n{BOLD}Estado del sistema despues de la Ronda 2:{RESET}")
    print(f"   {CYAN}Episodios acumulados: {n_episodes}{RESET}")
    print(f"   {MAGENTA}Patrones detectados: {n_patterns} (confianza: {pattern.confidence_score:.2f}){RESET}")
    print(f"   {WHITE}Concept graph: {n_edges} conexiones{RESET}")
    pause(0.5)


    # ─── SIMULAR 6 CONVERSACIONES MAS (para llegar a 12 antes de ronda 3) ────
    print(f"\n{YELLOW}[Simulando 6 conversaciones adicionales para consolidar el patron...]{RESET}")
    pause(0.3)

    extra_queries_2 = [
        "No puedo abrir los reportes trimestrales",
        "El acceso a reportes fue revocado sin razon",
        "Reportes del sistema no disponibles para mi usuario",
        "No veo los reportes en el panel",
        "Sistema de reportes dice acceso denegado",
        "Necesito acceso urgente a los reportes",
    ]
    extra_episodes_data_2 = [
        {"situation": "Sin acceso a reportes trimestrales", "strategy": "Verificar permisos y actualizar rol", "outcome": "Acceso restaurado", "session_arc": "resolved"},
        {"situation": "Acceso a reportes revocado inesperadamente", "strategy": "Revisar historial de cambios de permisos y restaurar", "outcome": "Permisos restaurados", "session_arc": "resolved"},
        {"situation": "Reportes no disponibles para el usuario", "strategy": "Agregar usuario al grupo de reportes en AD", "outcome": "Acceso habilitado", "session_arc": "resolved"},
        {"situation": "Reportes no visibles en el panel", "strategy": "Verificar permisos de vista y sincronizar", "outcome": "Reportes visibles", "session_arc": "resolved"},
        {"situation": "Acceso denegado al sistema de reportes", "strategy": "Resetear permisos desde consola de administracion", "outcome": "Acceso concedido", "session_arc": "resolved"},
        {"situation": "Acceso urgente a reportes requerido", "strategy": "Verificar y actualizar permisos de forma prioritaria", "outcome": "Acceso inmediato habilitado", "session_arc": "resolved"},
    ]

    async with SessionFactory() as db:
        for i, (q, ep_data) in enumerate(zip(extra_queries_2, extra_episodes_data_2)):
            emb = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.07)
            sess = await create_chat_session(db, user_id, tenant_id, area_id, f"Extra2 {i+1}")
            msgs = [
                ("user", q),
                ("assistant", "Revisando permisos del sistema de reportes."),
                ("user", "Gracias, ya puedo acceder!"),
                ("assistant", "Perfecto, acceso restaurado correctamente."),
            ]
            await add_messages(db, sess.id, msgs)
            ep = await extract_episode_mock(sess.id, area_id, tenant_id, db, ep_data, emb)
            ep.quality_score = round(0.74 + random.uniform(0, 0.12), 2)
            await db.commit()
            await update_concept_graph(ep, area_id, db)
            episode_counter += 1
            all_episodes_with_emb.append((ep, emb))
            print(f"   {GREEN}✓{RESET} Episodio #{episode_counter}: \"{q[:50]}\"")
            pause(0.1)

    # Actualizar patron con todos los episodios
    async with SessionFactory() as db:
        from app.models.cme import AreaEpisode
        from sqlalchemy import select
        eps_q = await db.execute(
            select(AreaEpisode).where(
                AreaEpisode.area_id == area_id,
                AreaEpisode.extraction_status == "completed"
            )
        )
        db_episodes = eps_q.scalars().all()
        eps_with_emb = [(ep, json.loads(ep.situation_embedding)) for ep in db_episodes if ep.situation_embedding]
        pattern = await run_pattern_detection_mock(area_id, tenant_id, db, eps_with_emb)

    pause(0.3)

    # ─── RONDA 3 ──────────────────────────────────────────────────────────────
    section("RONDA 3: Despues de 12 conversaciones (patron consolidado)")
    pause(0.3)

    conv = CONVERSATIONS[2]
    query_emb_3 = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.03)

    print(f"\n{BOLD}Marcelo pregunta:{RESET}")
    q = conv["user_query"]
    print(f"   {CYAN}{q}{RESET}")
    pause(0.4)

    # Mostrar contexto ANTES (ahora con patron consolidado)
    print(f"\n{BOLD}Contexto inyectado al LLM ANTES de responder:{RESET}")
    async with SessionFactory() as db:
        payload_r3 = await get_context_enricher_payload(
            conv["user_query"], area_id, tenant_id, db, query_emb_3
        )
    show_context_payload(payload_r3)
    pause(0.5)

    print(f"\n{YELLOW}[Conversacion en curso...]{RESET}")
    for role, content in conv["messages"]:
        icon = "💬" if role == "user" else "🤖"
        name = "Marcelo" if role == "user" else "Bot"
        print(f"   {icon} {BOLD}{name}:{RESET} {content[:80]}")
        pause(0.15)

    print(f"\n{GREEN}✅ Sesion terminada{RESET}")
    pause(0.3)

    # Extraer episodio de ronda 3
    print(f"\n{YELLOW}📚 Extrayendo episodio...{RESET}")
    episode_emb_3 = make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.04)

    async with SessionFactory() as db:
        session3 = await create_chat_session(db, user_id, tenant_id, area_id, "Acceso reportes 3")
        await add_messages(db, session3.id, conv["messages"])
        episode3 = await extract_episode_mock(
            session3.id, area_id, tenant_id, db, conv["episode_data"], episode_emb_3
        )
        episode3.quality_score = conv["quality"]
        await db.commit()
        await update_concept_graph(episode3, area_id, db)
        episode_counter += 1

        n_episodes = await count_episodes(area_id, db)
        n_patterns = await count_patterns(area_id, db)
        n_edges = await count_concept_edges(area_id, db)
        avg_quality = await get_avg_quality(area_id, db)

    show_episode(episode3, episode_counter)

    print(f"\n{BOLD}{GREEN}El LLM ahora tiene CONTEXTO ESPECIFICO -> respuesta mas precisa y directa{RESET}")
    pause(0.5)


    # ─── COMPARACION ANTES vs DESPUES ─────────────────────────────────────────
    section("COMPARACION: ANTES vs DESPUES del aprendizaje")
    pause(0.3)

    print(f"\n{BOLD}{RED}RONDA 1 - Sin memoria (el LLM responde a ciegas):{RESET}")
    print(f"   Contexto inyectado: {CYAN}[NINGUNO]{RESET}")
    print(f"   El LLM tiene que adivinar la solucion desde cero.")
    print(f"   Respuesta tipica: \"Voy a revisar tu acceso...\" (generica)")
    pause(0.4)

    print(f"\n{BOLD}{YELLOW}RONDA 2 - Con episodios (el LLM tiene precedentes):{RESET}")
    if payload_r2_before:
        lines = [l for l in payload_r2_before.split("\n") if l.strip() and not l.startswith("#")]
        for l in lines[:3]:
            print(f"   {GREEN}{l}{RESET}")
    else:
        print(f"   {CYAN}[Solo episodios similares disponibles]{RESET}")
    print(f"   El LLM puede decir: \"Resolvimos algo similar antes...\"")
    pause(0.4)

    print(f"\n{BOLD}{GREEN}RONDA 3 - Con patron consolidado (el LLM sabe exactamente que hacer):{RESET}")
    if payload_r3:
        lines = [l for l in payload_r3.split("\n") if l.strip() and not l.startswith("#")]
        for l in lines[:4]:
            print(f"   {GREEN}{l}{RESET}")
    print(f"   El LLM puede decir: \"Basandome en el historial, esto se resuelve verificando permisos\"")
    pause(0.5)

    # ─── RESUMEN FINAL ────────────────────────────────────────────────────────
    header("RESUMEN: QUE APRENDIO EL SISTEMA")
    pause(0.3)

    async with SessionFactory() as db:
        from app.models.cme import AreaPattern, AreaKnowledgeGap
        from sqlalchemy import select

        pats_q = await db.execute(
            select(AreaPattern).where(AreaPattern.area_id == area_id)
        )
        all_patterns = pats_q.scalars().all()

        gaps_q = await db.execute(
            select(AreaKnowledgeGap).where(
                AreaKnowledgeGap.area_id == area_id,
                AreaKnowledgeGap.status == "pending"
            )
        )
        gaps = gaps_q.scalars().all()

    print(f"\n{BOLD}{CYAN}📚 Episodios acumulados: {n_episodes}{RESET}")
    print(f"{BOLD}{MAGENTA}🔍 Patrones detectados: {len(all_patterns)}{RESET}")

    for p in all_patterns:
        print(f"   {YELLOW}→ \"{p.trigger_description}\" → \"{p.response_description[:60]}\" (confianza: {p.confidence_score:.2f}){RESET}")

    print(f"\n{BOLD}{WHITE}🕸️  Concept graph: {n_edges} conexiones entre conceptos{RESET}")

    if gaps:
        print(f"\n{BOLD}{RED}⚠️  Knowledge gaps: {len(gaps)} (temas sin respuesta consolidada){RESET}")
        for g in gaps:
            print(f"   → \"{g.topic_description[:60]}\" (ocurrencias: {g.occurrence_count})")
    else:
        print(f"\n{BOLD}{GREEN}⚠️  Knowledge gaps: 0 (el sistema sabe responder este tema){RESET}")

    print(f"\n{BOLD}{GREEN}📈 Quality score promedio: {avg_quality}{RESET}")

    # ─── EXPLICACION DEL VALOR ────────────────────────────────────────────────
    print(f"\n{YELLOW}{line()}{RESET}")
    print(f"{BOLD}{WHITE}POR QUE ESTO IMPORTA:{RESET}")
    print(f"{line()}{RESET}")
    print(f"""
{GREEN}Sin CME:{RESET}
   Cada conversacion empieza desde cero.
   El LLM no sabe que este problema ya fue resuelto 12 veces.
   Tiempo de resolucion: variable, depende del LLM.

{GREEN}Con CME (despues de 12 conversaciones):{RESET}
   El sistema inyecta automaticamente:
   - El patron detectado: "cuando hay problemas de acceso a reportes,
     verificar permisos" (confianza: {pattern.confidence_score:.2f})
   - Episodios similares resueltos exitosamente
   - El mecanismo causal: por que funciona esta solucion

{GREEN}Resultado:{RESET}
   El LLM responde con CERTEZA en lugar de explorar.
   La primera respuesta ya es la correcta.
   El usuario resuelve su problema en menos intercambios.
   El sistema mejora AUTOMATICAMENTE con cada conversacion.
""")

    header("FIN DE LA DEMO", char="═")
    print(f"\n{BOLD}{CYAN}El CME esta listo para aprender de las conversaciones reales de tu organizacion.{RESET}\n")

    # Cleanup
    await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Verificar dependencias
    missing = []
    try:
        import sqlalchemy
    except ImportError:
        missing.append("sqlalchemy")
    try:
        import aiosqlite
    except ImportError:
        missing.append("aiosqlite")

    if missing:
        print(f"ERROR: Faltan dependencias: {', '.join(missing)}")
        print(f"Instalar con: pip install {' '.join(missing)}")
        sys.exit(1)

    # Agregar el directorio actual al path para importar app.*
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    # Mockear get_embedding para no necesitar Ollama
    async def mock_get_embedding(text):
        """Mock de get_embedding que retorna un embedding similar al BASE."""
        return make_similar_embedding(BASE_ACCESS_EMBEDDING, noise=0.05)

    # Aplicar el mock antes de ejecutar
    import app.rag as rag_module
    rag_module.get_embedding = mock_get_embedding

    asyncio.run(run_demo())



