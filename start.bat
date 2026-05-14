@echo off
chcp 65001 >nul
echo ========================================
echo         ORGAN-IA - Iniciando...
echo ========================================
echo.

REM Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python no encontrado. Instala Python 3.12+
    pause
    exit /b 1
)
echo [OK] Python encontrado

REM Instalar dependencias si faltan
pip show fastapi >nul 2>&1
if %errorlevel% neq 0 (
    echo Instalando dependencias...
    pip install -r "%~dp0organ-ia\backend\requirements.txt"
)
echo [OK] Dependencias listas

REM Iniciar backend
echo Iniciando backend en puerto 8000...
start "Organ-IA Backend" cmd /k "cd /d "%~dp0organ-ia\backend" && python -m uvicorn main:app --port 8000 --reload"

echo Esperando que el backend arranque...
timeout /t 8 /nobreak >nul

REM Verificar que arranco
curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Backend no responde. Revisa la ventana del backend.
    pause
    exit /b 1
)

echo [OK] Backend corriendo en puerto 8000
echo.
echo ========================================
echo   ORGAN-IA LISTO
echo ========================================
echo.
echo   Abre: http://localhost:8000/login.html
echo.
echo   Credenciales:
echo   Email:    admin@organ-ia.ai
echo   Password: organia2026
echo.
echo ========================================
echo.

start http://localhost:8000/ui/index.html
pause