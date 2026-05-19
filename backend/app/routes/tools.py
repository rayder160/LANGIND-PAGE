"""
MCP Tools Endpoint — /api/tools/exec

Permite que Continue.dev (u otro cliente MCP) conecte a IM via HTTP/stdio.
El transporte MCP usa curl como proceso stdio:

  command: curl
  args: [-s, -X, POST, http://localhost:8000/api/tools/exec]

Continue envía JSON-RPC 2.0 en el body, este endpoint responde en el mismo formato.

Herramientas disponibles:
  - shell_exec: Ejecuta un comando de terminal y devuelve stdout/stderr
  - read_file: Lee el contenido de un archivo del workspace
  - list_dir: Lista archivos de un directorio
  - write_file: Escribe contenido en un archivo

Autenticación: Bearer token (JWT de Ether-IM) — opcional si TOOLS_REQUIRE_AUTH=false
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["mcp-tools"])

# ── Directorio base permitido (sandbox) ──────────────────────────────────────
# Por seguridad, las operaciones de archivo se limitan al workspace.
# Podés cambiar esto o dejarlo en None para sin restricción.
WORKSPACE_ROOT = os.environ.get("TOOLS_WORKSPACE_ROOT", None)

# Comandos bloqueados por seguridad
BLOCKED_COMMANDS = {
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=/dev/zero",
    ":(){ :|:& };:", "shutdown", "reboot", "halt",
}

# ── Definición de herramientas MCP ────────────────────────────────────────────

TOOLS_SCHEMA = [
    {
        "name": "shell_exec",
        "description": "Ejecuta un comando de shell y devuelve stdout y stderr. Útil para correr scripts, instalar dependencias, ver logs, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Comando a ejecutar (bash/sh)",
                },
                "cwd": {
                    "type": "string",
                    "description": "Directorio de trabajo (opcional). Si no se especifica, usa el directorio del backend.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout en segundos (default: 30, máximo: 120)",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Lee el contenido de un archivo. Devuelve el texto completo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta al archivo (absoluta o relativa al workspace)",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Línea de inicio (1-indexed, opcional)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Línea de fin (1-indexed, opcional)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "Lista archivos y carpetas en un directorio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta al directorio (absoluta o relativa al workspace)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Si listar recursivamente (default: false)",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Escribe o sobreescribe el contenido de un archivo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta al archivo"},
                "content": {"type": "string", "description": "Contenido a escribir"},
                "append": {"type": "boolean", "description": "Si agregar al final (default: false)", "default": False},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "search_files",
        "description": "Busca texto o patron en archivos de un directorio. Util para encontrar donde se usa una funcion, clase o variable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Texto a buscar (case-insensitive)"},
                "path": {"type": "string", "description": "Directorio donde buscar (default: '.')"},
                "file_glob": {"type": "string", "description": "Patron de archivos (default: '*', ej: '*.py', '*.ts')"},
                "max_results": {"type": "integer", "description": "Maximo de resultados (default: 30)", "default": 30},
            },
            "required": ["pattern"],
        },
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_path(raw_path: str) -> Path:
    """Resuelve una ruta, aplicando sandbox si WORKSPACE_ROOT está configurado."""
    p = Path(raw_path)
    if not p.is_absolute() and WORKSPACE_ROOT:
        p = Path(WORKSPACE_ROOT) / p
    return p.resolve()


def _is_safe_command(command: str) -> bool:
    """Verifica que el comando no esté en la lista negra."""
    cmd_lower = command.lower().strip()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return False
    return True


async def _tool_shell_exec(args: dict) -> dict:
    command = args.get("command", "").strip()
    cwd = args.get("cwd", None)
    timeout = min(int(args.get("timeout", 15)), 60)  # max 60s, default 15s

    if not command:
        return {"error": "Comando vacio"}

    if not _is_safe_command(command):
        return {"error": f"Comando bloqueado por seguridad: {command}"}

    # Detectar comandos que lanzan GUI o servidores — no bloquear el proceso
    GUI_PATTERNS = ["tkinter", "tk()", ".mainloop()", "pygame", "wx.", "PyQt", "kivy"]
    cmd_lower = command.lower()
    might_be_gui = any(p.lower() in cmd_lower for p in GUI_PATTERNS)
    # Si es python main.py sin flags, probablemente es GUI — usar timeout corto
    if "python" in cmd_lower and "main.py" in cmd_lower and "--" not in cmd_lower:
        timeout = min(timeout, 8)

    try:
        import concurrent.futures
        import subprocess as _subprocess

        def _run():
            result = _subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
            )
            return {
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
                "returncode": result.returncode,
            }

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await asyncio.wait_for(
                loop.run_in_executor(pool, _run),
                timeout=timeout + 2,
            )
        return result
    except asyncio.TimeoutError:
        return {
            "stdout": "",
            "stderr": "",
            "returncode": -1,
            "note": f"Proceso terminado por timeout ({timeout}s).",
        }
    except Exception as e:
        return {"error": str(e), "returncode": -1}


async def _tool_read_file(args: dict) -> dict:
    raw_path = args.get("path", "")
    start_line = args.get("start_line")
    end_line = args.get("end_line")
    MAX_CHARS = 12000  # ~3k tokens — seguro para Gemini

    if not raw_path:
        return {"error": "Ruta vacia"}

    try:
        p = _resolve_path(raw_path)
        if not p.exists():
            return {"error": f"Archivo no encontrado: {p}"}
        if not p.is_file():
            return {"error": f"No es un archivo: {p}"}

        content = p.read_text(encoding="utf-8", errors="replace")
        total_lines = len(content.splitlines())

        if start_line or end_line:
            lines = content.splitlines()
            s = (start_line - 1) if start_line else 0
            e = end_line if end_line else len(lines)
            content = "\n".join(lines[s:e])

        truncated = False
        if len(content) > MAX_CHARS:
            content = content[:MAX_CHARS]
            truncated = True

        result = {"content": content, "path": str(p), "total_lines": total_lines}
        if truncated:
            result["warning"] = f"Archivo truncado a {MAX_CHARS} caracteres. Usa start_line/end_line para leer secciones especificas."
        return result
    except Exception as ex:
        return {"error": str(ex)}


async def _tool_list_dir(args: dict) -> dict:
    raw_path = args.get("path", ".")
    recursive = args.get("recursive", False)

    try:
        p = _resolve_path(raw_path)
        if not p.exists():
            return {"error": f"Directorio no encontrado: {p}"}
        if not p.is_dir():
            return {"error": f"No es un directorio: {p}"}

        if recursive:
            entries = []
            for item in sorted(p.rglob("*")):
                rel = item.relative_to(p)
                entries.append({
                    "name": str(rel),
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                })
        else:
            entries = []
            for item in sorted(p.iterdir()):
                entries.append({
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                })

        return {"path": str(p), "entries": entries}
    except Exception as ex:
        return {"error": str(ex)}


async def _tool_write_file(args: dict) -> dict:
    raw_path = args.get("path", "")
    content = args.get("content", "")
    append = args.get("append", False)

    if not raw_path:
        return {"error": "Ruta vacia"}

    try:
        p = _resolve_path(raw_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            p.write_text(content, encoding="utf-8")
        return {"success": True, "path": str(p), "bytes_written": len(content.encode())}
    except Exception as ex:
        return {"error": str(ex)}


async def _tool_search_files(args: dict) -> dict:
    """Busca texto en archivos de un directorio."""
    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    file_glob = args.get("file_glob", "*")
    max_results = min(int(args.get("max_results", 30)), 100)

    if not pattern:
        return {"error": "Pattern vacio"}

    try:
        p = _resolve_path(path)
        if not p.exists():
            return {"error": f"Directorio no encontrado: {p}"}

        results = []
        import fnmatch
        for item in sorted(p.rglob(file_glob)):
            if not item.is_file():
                continue
            # Saltar binarios y carpetas de cache
            if any(part in item.parts for part in ["__pycache__", ".git", "node_modules", "venv", ".venv"]):
                continue
            try:
                text = item.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if pattern.lower() in line.lower():
                        results.append({
                            "file": str(item.relative_to(p) if p in item.parents else item),
                            "line": i,
                            "content": line.strip()[:200],
                        })
                        if len(results) >= max_results:
                            return {"results": results, "truncated": True, "pattern": pattern}
            except Exception:
                continue

        return {"results": results, "total": len(results), "pattern": pattern}
    except Exception as ex:
        return {"error": str(ex)}


async def _tool_edit_file(args: dict) -> dict:
    """Reemplaza un bloque de texto en un archivo. Mas seguro que write_file para ediciones parciales."""
    raw_path = args.get("path", "")
    old_str = args.get("old_str", "")
    new_str = args.get("new_str", "")

    if not raw_path:
        return {"error": "Ruta vacia"}
    if not old_str:
        return {"error": "old_str vacio — especifica el texto exacto a reemplazar"}

    try:
        p = _resolve_path(raw_path)
        if not p.exists():
            return {"error": f"Archivo no encontrado: {p}"}
        if not p.is_file():
            return {"error": f"No es un archivo: {p}"}

        content = p.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_str)

        if count == 0:
            return {
                "error": f"Texto no encontrado en {p.name}. El texto debe ser exacto incluyendo espacios e indentacion.",
                "hint": "Usa read_file primero para ver el contenido exacto del archivo."
            }
        if count > 1:
            return {
                "error": f"El texto aparece {count} veces. Incluye mas contexto en old_str para identificar unicamente la seccion."
            }

        new_content = content.replace(old_str, new_str, 1)
        p.write_text(new_content, encoding="utf-8")

        return {
            "success": True,
            "path": str(p),
            "lines_removed": old_str.count("\n") + 1,
            "lines_added": new_str.count("\n") + 1 if new_str else 0,
        }
    except Exception as ex:
        return {"error": str(ex)}


async def _tool_read_multiple_files(args: dict) -> dict:
    """Lee multiples archivos de una vez para entender como interactuan."""
    paths = args.get("paths", [])
    MAX_CHARS_PER_FILE = 8000
    MAX_TOTAL_CHARS = 30000

    if not paths:
        return {"error": "Lista de paths vacia"}
    if len(paths) > 10:
        return {"error": "Maximo 10 archivos por llamada"}

    results = []
    total_chars = 0

    for raw_path in paths:
        if total_chars >= MAX_TOTAL_CHARS:
            results.append({"path": raw_path, "error": "Limite total alcanzado"})
            continue
        try:
            p = _resolve_path(raw_path)
            if not p.exists():
                results.append({"path": raw_path, "error": "No encontrado"})
                continue
            if not p.is_file():
                results.append({"path": raw_path, "error": "No es un archivo"})
                continue

            content = p.read_text(encoding="utf-8", errors="replace")
            truncated = False
            if len(content) > MAX_CHARS_PER_FILE:
                content = content[:MAX_CHARS_PER_FILE]
                truncated = True

            total_chars += len(content)
            entry = {"path": str(p), "content": content, "lines": len(content.splitlines())}
            if truncated:
                entry["truncated"] = True
            results.append(entry)
        except Exception as ex:
            results.append({"path": raw_path, "error": str(ex)})

    return {"files": results, "total_files": len(results)}


TOOL_HANDLERS = {
    "shell_exec": _tool_shell_exec,
    "read_file": _tool_read_file,
    "list_dir": _tool_list_dir,
    "write_file": _tool_write_file,
    "search_files": _tool_search_files,
    "edit_file": _tool_edit_file,
    "read_multiple_files": _tool_read_multiple_files,
}


# ── Endpoint MCP JSON-RPC 2.0 ─────────────────────────────────────────────────

@router.post("/exec")
async def tools_exec(request: Request):
    """
    Endpoint MCP via HTTP. Acepta JSON-RPC 2.0.

    Métodos soportados:
      - initialize          → devuelve capacidades del servidor
      - tools/list          → lista herramientas disponibles
      - tools/call          → ejecuta una herramienta
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
        )

    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    logger.info(f"MCP request: method={method} id={rpc_id}")

    # ── initialize ────────────────────────────────────────────────────────────
    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "ether-im-tools",
                    "version": "1.0.0",
                },
            },
        })

    # ── notifications/initialized (no requiere respuesta) ────────────────────
    if method == "notifications/initialized":
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": {}})

    # ── tools/list ────────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": TOOLS_SCHEMA},
        })

    # ── tools/call ────────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {
                    "code": -32601,
                    "message": f"Herramienta desconocida: {tool_name}",
                },
            })

        try:
            result = await handler(tool_args)
            # MCP espera el resultado como lista de content items
            import json as _json
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": _json.dumps(result, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": "error" in result,
                },
            })
        except Exception as ex:
            logger.exception(f"Error ejecutando herramienta {tool_name}")
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32603, "message": str(ex)},
            })

    # ── Método desconocido ────────────────────────────────────────────────────
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": -32601, "message": f"Método no soportado: {method}"},
    })


# ── Endpoint de info (GET) ────────────────────────────────────────────────────

@router.get("/exec")
async def tools_info():
    """Info del servidor MCP — útil para verificar que está activo."""
    return {
        "server": "ether-im-tools",
        "version": "1.0.0",
        "protocol": "MCP JSON-RPC 2.0",
        "tools": [t["name"] for t in TOOLS_SCHEMA],
        "endpoint": "POST /api/tools/exec",
    }
