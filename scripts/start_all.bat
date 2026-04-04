@echo off
REM ============================================================
REM  SmartCat — Launch All Services
REM  1. Docker Qdrant (if not running)
REM  2. llama-server (Qwen3 32B, port 8080)
REM  3. FastAPI web server (port 8083)
REM  4. Cloudflare Tunnel (HTTPS → localhost:8083)
REM ============================================================

echo.
echo  ============================================================
echo   SmartCat — Starting All Services
echo  ============================================================
echo.

REM --- 1. Docker Qdrant ---
echo [1/4] Starting Qdrant...
docker start qdrant >nul 2>&1
if %errorlevel% neq 0 (
    echo   Qdrant container not found, creating...
    docker run -d --name qdrant -p 6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant
) else (
    echo   Qdrant started.
)

REM Wait for Qdrant to be ready
:wait_qdrant
timeout /t 2 /nobreak >nul
curl -s http://localhost:6333/health >nul 2>&1
if %errorlevel% neq 0 (
    echo   Waiting for Qdrant...
    goto wait_qdrant
)
echo   Qdrant ready on port 6333.
echo.

REM --- 2. llama-server ---
echo [2/4] Starting llama-server (Qwen3 32B)...
start "llama-server" cmd /c "g:\Proj\Agents1\llama-cpp\llama-server.exe --model g:\Proj\Agents1\Models\Qwen\Qwen3-32B-Q8_0.gguf --host 127.0.0.1 --port 8080 --n-gpu-layers 33 --ctx-size 32768 --threads 16 --alias qwen3-32b"

REM Wait for llama-server to be ready
:wait_llama
timeout /t 5 /nobreak >nul
curl -s http://127.0.0.1:8080/health >nul 2>&1
if %errorlevel% neq 0 (
    echo   Waiting for llama-server...
    goto wait_llama
)
echo   llama-server ready on port 8080.
echo.

REM --- 3. FastAPI web server ---
echo [3/4] Starting SmartCat web server...
start "smartcat-web" cmd /c "cd /d G:\Proj\SmartCat && .venv\Scripts\activate && set PYTHONPATH=src && python -m uvicorn smartcat.api.app:app --host 0.0.0.0 --port 8083"

REM Wait for FastAPI to be ready
:wait_fastapi
timeout /t 3 /nobreak >nul
curl -s http://localhost:8083/api/health >nul 2>&1
if %errorlevel% neq 0 (
    echo   Waiting for web server...
    goto wait_fastapi
)
echo   Web server ready on port 8083.
echo.

REM --- 4. Cloudflare Tunnel ---
echo [4/4] Starting Cloudflare Tunnel...
start "cloudflare-tunnel" cmd /c "E:\Downloads\cloudflared-windows-amd64.exe tunnel --url http://localhost:8083"

timeout /t 5 /nobreak >nul
echo   Tunnel starting... Check the tunnel window for the public URL.
echo.

echo  ============================================================
echo   All services started!
echo  ============================================================
echo.
echo   Local:   http://localhost:8083
echo   Qdrant:  http://localhost:6333
echo   LLM:     http://localhost:8080
echo   Tunnel:  Check "cloudflare-tunnel" window for HTTPS URL
echo.
echo   To stop all: run scripts\stop_all.bat
echo  ============================================================
echo.
pause
