@echo off
title ProxDeep Backend
color 0A

echo.
echo  ==========================================
echo   ProxDeep Backend - Iniciando...
echo  ==========================================
echo.

:: Posicionarse en la carpeta del bat (LANGIND-PAGE)
cd /d "%~dp0"

:: Verificar que existe el venv
if not exist "backend\venv\Scripts\python.exe" (
    echo.
    echo  [ERROR] No se encontro el entorno virtual.
    echo  Solucion: abre una terminal en la carpeta backend y ejecuta:
    echo    python -m venv venv
    echo    venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:: Verificar Ollama
echo  [1/3] Verificando Ollama...
curl -s http://localhost:11434 >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Ollama no detectado. Iniciando en segundo plano...
    start "" ollama serve
    timeout /t 4 /nobreak >nul
) else (
    echo  [OK] Ollama activo.
)

echo.
echo  [2/3] Preparando servidor...

:: Abrir navegador automaticamente despues de 5 segundos
start "" cmd /c "timeout /t 5 /nobreak >nul && start http://localhost:8000/index.html"

echo  [3/3] Levantando FastAPI en puerto 8000...
echo.
echo  ==========================================
echo   Abriendo: http://localhost:8000/index.html
echo   Login:    http://localhost:8000/login.html
echo   API docs: http://localhost:8000/docs
echo   Modelo:   gemma3:4b via Ollama
echo  ==========================================
echo.
echo  Presiona Ctrl+C para detener el servidor.
echo.

:: Entrar a backend y correr uvicorn con el python del venv
cd backend
venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

pause
