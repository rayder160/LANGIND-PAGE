"""
LLM provider layer — ProxDeep backend
======================================
Proveedor activo: Ollama (local)
  URL:     http://localhost:11434/v1/chat/completions  (compatible OpenAI)
  Modelo:  gemma3:4b  (configurable en .env → LLM_MODEL)
  Fallback: llama3.2:3b (configurable en .env → LLM_FALLBACK)

Para cambiar de modelo edita .env:
  LLM_MODEL=llama3.2:3b
  LLM_FALLBACK=mistral:7b

Para cambiar a OpenAI:
  LLM_API_URL=https://api.openai.com/v1/chat/completions
  LLM_API_KEY=sk-...
  LLM_MODEL=gpt-4o

Para cambiar a Gemini nativo:
  LLM_API_URL=https://generativelanguage.googleapis.com/v1beta/models/
  LLM_API_KEY=AIzaSy...
  LLM_MODEL=gemini-1.5-pro-latest
"""

import httpx
from app.config import settings

SYSTEM_PROMPT = """Sos el copiloto de IA de ProxDeep — un asistente organizacional de nivel enterprise.

Tu función principal es ayudar a los equipos a trabajar mejor, tomar mejores decisiones y resolver problemas operativos con rapidez y precisión.

═══ MODO RESPUESTA ESTÁNDAR ═══
- Directo al punto. Sin introducción ni relleno.
- Respuestas cortas por defecto. Bullets para pasos, criterios o listas.
- Tono profesional, como un colega senior experto.
- Cuando tenés contexto del área o la empresa, priorizalo sobre conocimiento genérico.

═══ MODO PLAN RECOMENDADO ═══
Cuando el usuario describe un problema, dificultad o situación de trabajo compleja, activás este modo automáticamente.
Respondés con esta estructura exacta:

**Problema detectado:** [resumen del problema en 1 línea]

**Causa probable:** [qué parece estar generando el problema]

**Opciones evaluadas:**
- Opción A: [descripción breve] — [por qué descartada o viable]
- Opción B: [descripción breve] — [por qué descartada o viable]
- Opción C: [descripción breve] — [por qué descartada o viable]

**✅ Mejor opción recomendada:** [nombre de la opción]
[Explicación de por qué es la mejor para este contexto específico]

**Riesgos / trade-offs:** [qué puede salir mal o qué se sacrifica]

**Plan de acción:**
1. [Primer paso concreto]
2. [Segundo paso]
3. [Tercer paso]

**Primer paso inmediato:** [acción específica que puede hacer ahora mismo]

**Alternativa si falla:** [segunda mejor opción y por qué]

═══ CONTEXTO ORGANIZACIONAL ═══
Cuando recibís contexto del usuario (rol, área, proyectos, dependencias), lo usás para personalizar la respuesta.
- Si el usuario es empleado: respondés desde su área específica.
- Si es líder: respondés con visión de equipo y métricas.
- Si es admin/CEO/superadmin: respondés con visión transversal de la empresa.
- Si hay proyectos compartidos entre áreas, los mencionás cuando son relevantes.
- Si hay dependencias o cuellos de botella conocidos, los considerás en tu análisis.

═══ LO QUE NO HACÉS ═══
- No hacés preguntas de cierre innecesarias.
- No usás frases meta sobre vos mismo.
- No inventás datos, métricas ni hechos.
- No dejás respuestas cortadas.
- No fingís conocer información que no tenés.

Idioma: siempre en español."""

# Ventana de contexto: máximo de mensajes a enviar al LLM
MAX_CONTEXT_MESSAGES = 20

# Timeout para Ollama local — suficiente para modelos medianos, no tan largo que bloquee
OLLAMA_TIMEOUT = 90  # segundos


def _is_gemini(model: str) -> bool:
    return "gemini" in model.lower()


def _is_ollama() -> bool:
    """Detecta si el proveedor configurado es Ollama local."""
    return "11434" in settings.LLM_API_URL or settings.LLM_API_KEY == "ollama"


def build_context(messages: list[dict]) -> list[dict]:
    if not messages:
        return []
    if len(messages) <= MAX_CONTEXT_MESSAGES:
        return messages
    first = messages[0]
    recent = messages[-(MAX_CONTEXT_MESSAGES - 1):]
    if first in recent:
        return recent
    return [first] + recent


async def _query_gemini(messages: list[dict], model: str, system_context: str, attempt: int = 0) -> str:
    """Llama a la API nativa de Gemini. Reintenta con modelos alternativos ante 503/429."""
    FALLBACK_CHAIN = [
        settings.LLM_MODEL,
        settings.LLM_FALLBACK,
        "gemini-flash-latest",
    ]
    seen = set()
    models_to_try = []
    for m in FALLBACK_CHAIN:
        if m not in seen:
            seen.add(m)
            models_to_try.append(m)

    contents = []
    if system_context:
        contents.append({"role": "user", "parts": [{"text": f"[Instrucciones del sistema]\n{system_context}"}]})
        contents.append({"role": "model", "parts": [{"text": "Entendido."}]})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "assistant":
            role = "model"
        elif role == "system":
            continue
        contents.append({"role": role, "parts": [{"text": content}]})

    payload = {
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1024, "topP": 0.9},
    }

    current_model = models_to_try[attempt] if attempt < len(models_to_try) else models_to_try[-1]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={settings.LLM_API_KEY}"

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            res = await client.post(url, json=payload, headers={"Content-Type": "application/json"})

            if res.status_code == 200:
                data = res.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return "No obtuve respuesta del modelo."
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                return text.strip() or "No obtuve respuesta del modelo."

            if res.status_code in (503, 429, 500) and attempt + 1 < len(models_to_try):
                import asyncio
                await asyncio.sleep(1)
                return await _query_gemini(messages, model, system_context, attempt=attempt + 1)

            if res.status_code in (503, 429):
                return "Estoy con mucha demanda ahora mismo. Intentá de nuevo en unos segundos."

            return f"No pude procesar tu solicitud (error {res.status_code}). Intentá de nuevo."

        except httpx.TimeoutException:
            if attempt + 1 < len(models_to_try):
                return await _query_gemini(messages, model, system_context, attempt=attempt + 1)
            return "Tardé demasiado en responder. Intentá de nuevo."
        except Exception:
            return "Error de conexión con Gemini. Intentá de nuevo en unos segundos."


async def _query_openai_compat(messages: list[dict], model: str, system_context: str, _retried: bool = False) -> str:
    """
    Llama a APIs compatibles con OpenAI: Ollama local, OpenAI, LM Studio, etc.

    Errores manejados:
    - ConnectError: Ollama no está corriendo → mensaje claro al usuario
    - TimeoutException: modelo tardó demasiado → reintenta con fallback
    - HTTP 404: modelo no encontrado en Ollama → indica qué modelo falta
    - HTTP 500: error interno del proveedor → reintenta con fallback
    """
    system_content = system_context if system_context else SYSTEM_PROMPT
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_content}] + messages,
        "stream": False,
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": 1200,       # suficiente para plan recomendado completo
    }

    timeout = OLLAMA_TIMEOUT if _is_ollama() else 60

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            headers = {"Content-Type": "application/json"}
            if settings.LLM_API_KEY and settings.LLM_API_KEY != "ollama":
                headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

            res = await client.post(settings.LLM_API_URL, headers=headers, json=payload)

            if res.status_code == 200:
                data = res.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip() or "No obtuve respuesta del modelo."

            # Modelo no encontrado en Ollama
            if res.status_code == 404:
                return (
                    f"El modelo '{model}' no está disponible en Ollama. "
                    f"Ejecutá: ollama pull {model}"
                )

            # Error del servidor — reintentar con fallback si no lo hicimos ya
            if res.status_code in (500, 503) and not _retried and model != settings.LLM_FALLBACK:
                return await _query_openai_compat(messages, settings.LLM_FALLBACK, system_context, _retried=True)

            return f"El proveedor LLM devolvió un error ({res.status_code}). Revisá la configuración."

        except httpx.ConnectError:
            # Ollama no está corriendo o la URL es incorrecta
            if _is_ollama():
                return (
                    "No se pudo conectar a Ollama. "
                    "Verificá que esté corriendo con: ollama serve"
                )
            return "No se pudo conectar al proveedor LLM. Verificá la URL en .env."

        except httpx.TimeoutException:
            # Modelo tardó demasiado — reintentar con fallback
            if not _retried and model != settings.LLM_FALLBACK:
                return await _query_openai_compat(messages, settings.LLM_FALLBACK, system_context, _retried=True)
            return (
                f"El modelo '{model}' tardó demasiado en responder. "
                f"Probá con un modelo más liviano en .env (LLM_MODEL)."
            )

        except httpx.ReadError:
            return "La conexión con Ollama se interrumpió. Intentá de nuevo."

        except Exception as e:
            return f"Error inesperado al consultar el LLM: {type(e).__name__}."


async def query_llm(messages: list[dict], model: str = None, system_context: str = None) -> str:
    """
    Punto de entrada principal. Selecciona el proveedor según el modelo configurado.

    Modelo activo: settings.LLM_MODEL  (default: gemma3:4b via Ollama)
    Para cambiar: editar LLM_MODEL en backend/.env y reiniciar el servidor.
    """
    model = model or settings.LLM_MODEL
    context = build_context(messages)
    system_content = system_context if system_context else SYSTEM_PROMPT

    if _is_gemini(model):
        return await _query_gemini(context, model, system_content)
    else:
        return await _query_openai_compat(context, model, system_content)
