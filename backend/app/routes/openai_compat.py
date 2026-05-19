"""
OpenAI-Compatible API - Endpoint /v1/chat/completions

Permite conectar Ether-IM a herramientas como Continue.dev en VS Code.
IM responde con toda su memoria cognitiva activa (episodios, patrones,
forgetting curve) igual que en el chat normal.

Autenticacion: Bearer token (el mismo JWT de Ether-IM)
Modelo: cualquier string - se ignora, siempre usa IM con su cerebro completo
"""
import re
import os
import json
import time
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Any

from app.database import get_db
from app.models import User, ChatSession, ChatMessage
from app.routes.auth import get_current_user
from app.llm import query_llm, SYSTEM_PROMPT as IM_IDENTITY
from app.memory import get_area_context, maybe_update_memory
from app.rag import search_relevant, index_conversation

# CME - fail-silent
try:
    from app.cme.working_memory import working_memory as cme_working_memory
    from app.cme.context_enricher import context_enricher
    from app.cme.session_processor import process_session_signals
    from app.database import AsyncSessionLocal
    CME_AVAILABLE = True
except ImportError:
    CME_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai-compat"])

MAX_TOOL_ITERATIONS = 20

VSCODE_TOOLS_CONTEXT = """
---
[Contexto: estas operando dentro de VS Code a traves de Continue]

Tenes acceso a herramientas reales. El backend las ejecuta automaticamente y te devuelve el resultado.

Para usar una herramienta, responde UNICAMENTE con este formato (sin texto antes ni despues):

TOOL_CALL: {"tool": "<nombre>", "params": {<parametros>}}

Herramientas disponibles:

EXPLORAR:
- list_dir: lista archivos. {"path": ".", "recursive": false}
- read_file: lee un archivo. {"path": "main.py"} o con rango {"path": "main.py", "start_line": 1, "end_line": 50}
- read_multiple_files: lee varios archivos a la vez. {"paths": ["a.py", "b.py"]}
- search_files: busca texto en archivos. {"pattern": "def login", "path": ".", "file_glob": "*.py"}

MODIFICAR:
- edit_file: reemplaza texto exacto en un archivo. {"path": "main.py", "old_str": "texto exacto", "new_str": "texto nuevo"}
- write_file: escribe/sobreescribe un archivo completo. {"path": "nuevo.py", "content": "..."}

EJECUTAR:
- shell_exec: ejecuta un comando. {"command": "pip list", "cwd": "."}

Flujo recomendado para editar codigo:
1. list_dir para ver la estructura
2. read_multiple_files para entender como interactuan los archivos relevantes
3. search_files para encontrar exactamente donde esta lo que queres cambiar
4. edit_file para hacer el cambio preciso
5. Verificar con read_file o shell_exec si aplica

Despues de recibir el resultado, responde de forma concisa.
Formato para archivos: usa arbol con indentacion (├── ), NO listas con bullets.
Podes encadenar multiples herramientas en una sola conversacion.
NUNCA uses os.listdir(), ls, cat, open() ni codigo Python para acceder a archivos.
NUNCA generes bloques de codigo markdown para modificar archivos. Usa SIEMPRE edit_file o write_file directamente — los cambios se aplican solos sin que el usuario tenga que hacer nada.
No muestres el TOOL_CALL en tu respuesta al usuario — ejecutalo internamente y reporta solo el resultado ("Listo, cambié X en archivo Y").
Para tareas grandes (crear un backend completo, refactorizar varios archivos), trabaja archivo por archivo. Despues de cada archivo creado o modificado, reporta brevemente lo que hiciste y continua con el siguiente sin esperar confirmacion del usuario. Al final lista todos los archivos que creaste/modificaste.
---"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(content) -> str:
    """Extrae texto de content que puede ser string o lista de partes."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content) if content else ""


def _extract_workspace_path(messages: list) -> str | None:
    """
    Extrae el workspace path que Continue inyecta en los mensajes de sistema.
    Busca patrones como 'Current directory: C:\\...' o paths de Windows/Unix.
    """
    for msg in messages:
        if msg.role == "system":
            content = _extract_text(msg.content)
            for line in content.splitlines():
                line_lower = line.lower()
                if any(k in line_lower for k in ("current directory", "workspace", "working directory", "cwd")):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        path = parts[1].strip()
                        if path and len(path) > 2:
                            return path
            # Windows path: C:\Users\...
            win_match = re.search(r'[A-Za-z]:\\[^\s\n"\'<>|*?]+', content)
            if win_match:
                return win_match.group(0).rstrip('.,;)')
            # Unix path
            unix_match = re.search(r'/(?:home|Users|workspace|projects|code|srv)/[^\s\n"\']+', content)
            if unix_match:
                return unix_match.group(0).rstrip('.,;)')
    return None


def _resolve_tool_paths(tool_params: dict, workspace_path: str) -> dict:
    """Resuelve paths relativos contra el workspace del usuario."""
    def resolve(p: str) -> str:
        if not p:
            return workspace_path
        # Ya es absoluto
        if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
            return p
        # Relativo — resolver contra workspace
        return os.path.normpath(os.path.join(workspace_path, p))

    params = dict(tool_params)
    if "path" in params:
        params["path"] = resolve(params["path"])
    if "paths" in params and isinstance(params["paths"], list):
        params["paths"] = [resolve(p) for p in params["paths"]]
    if "cwd" in params:
        params["cwd"] = resolve(params["cwd"])
    return params


def _parse_tool_call(response_text: str):
    """
    Detecta si IM quiere llamar una herramienta.
    Formato: TOOL_CALL: {"tool": "...", "params": {...}}
    Devuelve (tool_name, params) o None.
    """
    match = re.search(r'TOOL_CALL:\s*(\{.*?\})\s*$', response_text, re.DOTALL | re.MULTILINE)
    if not match:
        # Intentar con el JSON mas largo posible
        match = re.search(r'TOOL_CALL:\s*(\{.*\})', response_text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        tool_name = data.get("tool", "")
        params = data.get("params", {})
        if tool_name:
            return tool_name, params
    except json.JSONDecodeError:
        pass
    return None


async def _execute_tool(tool_name: str, params: dict) -> str:
    """Ejecuta una herramienta internamente y devuelve el resultado como string."""
    from app.routes.tools import (
        _tool_shell_exec, _tool_read_file, _tool_list_dir,
        _tool_write_file, _tool_search_files, _tool_edit_file,
        _tool_read_multiple_files
    )
    handlers = {
        "shell_exec": _tool_shell_exec,
        "read_file": _tool_read_file,
        "list_dir": _tool_list_dir,
        "write_file": _tool_write_file,
        "search_files": _tool_search_files,
        "edit_file": _tool_edit_file,
        "read_multiple_files": _tool_read_multiple_files,
    }
    handler = handlers.get(tool_name)
    if not handler:
        return f"[Error: herramienta '{tool_name}' no existe. Disponibles: {list(handlers.keys())}]"
    try:
        result = await handler(params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"[Error ejecutando {tool_name}: {e}]"


def _build_response(content: str, model: str = "ether-im") -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
    }


# ── Modelos Pydantic ──────────────────────────────────────────────────────────

class OAIMessage(BaseModel):
    role: str
    content: str | list | None = None


class OAIChatRequest(BaseModel):
    model: str = "ether-im"
    messages: list[OAIMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop: Any = None
    tools: list[dict] | None = None
    tool_choice: Any = None


# ── Endpoint principal ────────────────────────────────────────────────────────

@router.post("/chat/completions")
async def chat_completions(
    data: OAIChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_messages = [m for m in data.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No hay mensajes de usuario.")

    last_user_content = _extract_text(user_messages[-1].content)
    if not last_user_content.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacio.")

    # Log system message para debug de workspace detection
    for msg in data.messages:
        if msg.role == "system":
            logger.info(f"[system-msg] {_extract_text(msg.content)[:400]}")

    # Historial interno (sin system messages — los manejamos nosotros)
    history = []
    for msg in data.messages:
        if msg.role == "system":
            continue
        text = _extract_text(msg.content)
        if text:
            role = "assistant" if msg.role == "assistant" else "user"
            history.append({"role": role, "content": text})

    # Sesion VS Code
    session = ChatSession(
        user_id=user.id,
        tenant_id=user.tenant_id,
        area_id=user.area_id,
        title=f"[VS Code] {last_user_content[:40]}",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    session_id = session.id

    db.add(ChatMessage(session_id=session_id, role="user", content=last_user_content))
    await db.commit()

    # Contexto del area
    area_context = None
    if user.area_id:
        area_context = await get_area_context(user.area_id, db)

    if user.area_id and len(last_user_content.split()) > 3:
        try:
            relevant_chunks = await search_relevant(user.area_id, last_user_content, db)
            if relevant_chunks:
                rag_ctx = "\n\n".join(relevant_chunks)
                area_context = (area_context + f"\n\nInformacion relevante:\n{rag_ctx}") if area_context else rag_ctx
        except Exception:
            pass

    if CME_AVAILABLE and user.area_id and len(last_user_content.split()) > 3:
        try:
            await cme_working_memory.update_topic(session_id, last_user_content)
            await cme_working_memory.update_emotion(session_id, last_user_content)
            wm_ctx = await cme_working_memory.get_or_create(session_id)
            cme_payload = await context_enricher.enrich(
                query=last_user_content,
                area_id=user.area_id,
                tenant_id=user.tenant_id,
                working_memory=wm_ctx,
                db=db,
                user_id=user.id,
            )
            if cme_payload:
                area_context = (area_context + f"\n\n{cme_payload}") if area_context else cme_payload
        except Exception:
            pass

    # System prompt
    combined_system = IM_IDENTITY + VSCODE_TOOLS_CONTEXT
    if area_context:
        combined_system += f"\n\n---\n{area_context}"

    # Detectar workspace del usuario
    workspace_path = _extract_workspace_path(data.messages)
    if workspace_path:
        combined_system += f"\n\n[Workspace activo: {workspace_path}]\nUsa este path como base para todas las herramientas cuando el usuario diga 'este proyecto' o 'el directorio actual'."
        logger.info(f"[workspace] detectado: {workspace_path}")
    else:
        logger.info("[workspace] no detectado en system messages")

    # ── Tool loop ─────────────────────────────────────────────────────────────

    async def run_tool_loop(working_history: list, sys_prompt: str, ws_path: str | None) -> str:
        response_text = ""
        for iteration in range(MAX_TOOL_ITERATIONS):
            response_text = await query_llm(working_history, system_context=sys_prompt)
            logger.info(f"[tool-loop] iter={iteration} resp={response_text[:120]}")

            tool_call = _parse_tool_call(response_text)
            if not tool_call:
                break

            tool_name, tool_params = tool_call
            if ws_path:
                tool_params = _resolve_tool_paths(tool_params, ws_path)

            logger.info(f"[tool-loop] ejecutando {tool_name} params={tool_params}")
            tool_result = await _execute_tool(tool_name, tool_params)
            logger.info(f"[tool-loop] resultado {tool_name}: {tool_result[:200]}")

            working_history.append({"role": "assistant", "content": response_text})
            working_history.append({
                "role": "user",
                "content": f"[Resultado de {tool_name}]:\n{tool_result}\n\nSigue con la tarea. Si terminaste, responde al usuario con un resumen breve de lo que hiciste (sin mostrar codigo ni TOOL_CALL)."
            })
        # Limpiar cualquier TOOL_CALL de la respuesta final
        response_text = re.sub(r'TOOL_CALL:\s*\{.*?\}', '', response_text, flags=re.DOTALL).strip()
        return response_text

    if data.stream:
        import json as _json

        async def stream_response():
            working_history = list(history)
            final_text = ""

            for iteration in range(MAX_TOOL_ITERATIONS):
                resp = await query_llm(working_history, system_context=combined_system)
                logger.info(f"[stream-loop] iter={iteration} resp={resp[:120]}")

                tool_call = _parse_tool_call(resp)
                if not tool_call:
                    final_text = resp
                    break

                tool_name, tool_params = tool_call
                if workspace_path:
                    tool_params = _resolve_tool_paths(tool_params, workspace_path)

                # Progreso visible
                progress_msg = f"\n*Ejecutando `{tool_name}`...*\n"
                yield f"data: {_json.dumps({'id': f'chatcmpl-{uuid.uuid4().hex[:8]}', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': data.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': progress_msg}, 'finish_reason': None}]})}\n\n"

                tool_result = await _execute_tool(tool_name, tool_params)
                working_history.append({"role": "assistant", "content": resp})
                working_history.append({
                    "role": "user",
                    "content": f"[Resultado de {tool_name}]:\n{tool_result}\n\nAhora responde al usuario con esta informacion."
                })
                final_text = resp

            # Respuesta final
            yield f"data: {_json.dumps({'id': f'chatcmpl-{uuid.uuid4().hex[:8]}', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': data.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': final_text}, 'finish_reason': None}]})}\n\n"
            yield f"data: {_json.dumps({'id': f'chatcmpl-{uuid.uuid4().hex[:8]}', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': data.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"

            try:
                db.add(ChatMessage(session_id=session_id, role="assistant", content=final_text))
                await db.commit()
            except Exception:
                pass

        return StreamingResponse(stream_response(), media_type="text/event-stream")

    # Non-streaming
    response_text = await run_tool_loop(list(history), combined_system, workspace_path)

    db.add(ChatMessage(session_id=session_id, role="assistant", content=response_text))
    await db.commit()

    if user.area_id and len(last_user_content.split()) > 3:
        try:
            await index_conversation(user.area_id, last_user_content, response_text, db)
            await maybe_update_memory(user.area_id, db)
        except Exception:
            pass

    if CME_AVAILABLE and user.area_id and user.tenant_id:
        try:
            import asyncio
            asyncio.create_task(process_session_signals(
                session_id=session_id,
                area_id=user.area_id,
                tenant_id=user.tenant_id,
                user_id=user.id,
                db_factory=AsyncSessionLocal,
            ))
        except Exception:
            pass

    return _build_response(response_text, model=data.model)


# ── Modelos disponibles ───────────────────────────────────────────────────────

@router.get("/models")
async def list_models(user: User = Depends(get_current_user)):
    return {
        "object": "list",
        "data": [{
            "id": "ether-im",
            "object": "model",
            "created": 1700000000,
            "owned_by": "ether-im",
            "description": "IM - Agente cognitivo con memoria episodica, patrones y forgetting curve.",
        }],
    }


# ── Autocompletado ────────────────────────────────────────────────────────────

class OAICompletionRequest(BaseModel):
    model: str = "ether-im"
    prompt: str
    suffix: str | None = None
    max_tokens: int | None = 256
    temperature: float | None = 0.2
    stop: Any = None
    stream: bool = False


@router.post("/completions")
async def completions(
    data: OAICompletionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not data.prompt or not data.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt vacio.")

    autocomplete_system = """Sos un asistente de autocompletado de codigo.
Reglas:
- Responde SOLO con el codigo que completa el fragmento, sin explicaciones
- No repitas el codigo que ya existe
- Mantene el mismo estilo e indentacion del codigo existente
- Si hay un sufijo, asegurate de que tu completado encaje perfectamente
- Maximo 20 lineas"""

    suffix_context = f"\n\n# Codigo que viene despues:\n{data.suffix}" if data.suffix else ""
    messages = [{"role": "user", "content": f"# Completa este codigo:\n{data.prompt}{suffix_context}"}]
    completion_text = await query_llm(messages, system_context=autocomplete_system)

    if "```" in completion_text:
        lines = completion_text.split("\n")
        completion_text = "\n".join(l for l in lines if not l.strip().startswith("```"))

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:12]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": data.model,
        "choices": [{"text": completion_text, "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
    }
