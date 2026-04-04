@echo off
REM ============================================================
REM  SmartCat — Stop All Services
REM ============================================================

echo Stopping all SmartCat services...

echo [1/4] Stopping Cloudflare Tunnel...
taskkill /IM cloudflared-windows-amd64.exe /F >nul 2>&1

echo [2/4] Stopping web server...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8083.*LISTENING"') do taskkill /PID %%a /F >nul 2>&1

echo [3/4] Stopping llama-server...
taskkill /IM llama-server.exe /F >nul 2>&1

echo [4/4] Stopping Qdrant...
docker stop qdrant >nul 2>&1

echo.
echo All services stopped.
pause
