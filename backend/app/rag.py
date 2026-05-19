"""
RAG — Retrieval Augmented Generation para memoria organizacional.
Usa nomic-embed-text (Ollama) para embeddings y búsqueda coseno en SQLite.
Sin dependencias externas de vectorDB — todo en la DB existente.
"""
import json
import math
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.models import AreaChunk
from app.config import settings

EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
TOP_K = 3  # chunks más relevantes a recuperar


async def get_embedding(text_input: str) -> list[float] | None:
    """Genera embedding para un texto usando Ollama."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(EMBED_URL, json={"model": EMBED_MODEL, "prompt": text_input})
            if res.status_code == 200:
                return res.json().get("embedding")
    except Exception:
        pass
    return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def store_chunk(area_id: str, content: str, source: str, db: AsyncSession) -> None:
    """Genera embedding y guarda un chunk de conocimiento del área."""
    embedding = await get_embedding(content)
    if not embedding:
        return
    chunk = AreaChunk(
        area_id=area_id,
        content=content,
        source=source,
        embedding=json.dumps(embedding),
    )
    db.add(chunk)
    await db.commit()


async def search_relevant(area_id: str, query: str, db: AsyncSession) -> list[str]:
    """Busca los chunks más relevantes para una query."""
    query_emb = await get_embedding(query)
    if not query_emb:
        return []

    result = await db.execute(
        select(AreaChunk).where(AreaChunk.area_id == area_id)
    )
    chunks = result.scalars().all()
    if not chunks:
        return []

    scored = []
    for chunk in chunks:
        try:
            emb = json.loads(chunk.embedding)
            score = cosine_similarity(query_emb, emb)
            scored.append((score, chunk.content))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    return [content for _, content in scored[:TOP_K] if _ > 0.3]


async def index_conversation(area_id: str, user_msg: str, bot_response: str, db: AsyncSession) -> None:
    """
    Indexa un par pregunta-respuesta como chunk de conocimiento del área.
    Chunking semántico: agrupa mensajes del mismo tema antes de indexar.
    Solo indexa si la respuesta parece útil.
    """
    if len(bot_response) < 30:
        return
    if any(x in bot_response.lower() for x in ["no tengo esa información", "error de conexión", "no pude"]):
        return

    # Detectar si es el mismo tema que el chunk anterior (por palabras clave compartidas)
    user_words = set(w.lower() for w in user_msg.split() if len(w) > 4)

    # Buscar el chunk más reciente del área para ver si es el mismo tema
    recent_q = await db.execute(
        select(AreaChunk)
        .where(AreaChunk.area_id == area_id, AreaChunk.source == "conversation")
        .order_by(AreaChunk.created_at.desc())
        .limit(1)
    )
    recent = recent_q.scalar_one_or_none()

    if recent:
        recent_words = set(w.lower() for w in recent.content.split() if len(w) > 4)
        overlap = len(user_words & recent_words) / max(len(user_words), 1)

        # Si hay >30% de palabras en común, es el mismo tema — enriquecer el chunk existente
        if overlap > 0.3 and len(recent.content) < 800:
            recent.content += f"\n\nPregunta: {user_msg}\nRespuesta: {bot_response}"
            await db.commit()
            return

    # Nuevo tema — crear chunk nuevo
    content = f"Pregunta: {user_msg}\nRespuesta: {bot_response}"
    await store_chunk(area_id, content, "conversation", db)


def is_ambiguous(query: str) -> bool:
    """Detecta si una pregunta es demasiado vaga para responder bien."""
    # Saludos y frases cortas normales — no son ambiguas
    greetings = ["hola", "hi", "hey", "buenos días", "buenas tardes", "buenas noches", "buenas", "qué tal", "cómo estás"]
    query_lower = query.lower().strip()
    if any(query_lower.startswith(g) for g in greetings):
        return False

    vague_patterns = [
        "qué hago", "cómo funciona", "explícame todo", "cuéntame todo",
        "qué es esto", "no sé qué", "qué pasa con todo",
    ]
    # Solo marcar como ambiguo si es muy corto Y no es un saludo
    if len(query_lower.split()) <= 1 and query_lower not in greetings:
        return True
    return any(p in query_lower for p in vague_patterns) and len(query_lower) < 20
