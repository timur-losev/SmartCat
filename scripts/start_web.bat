@echo off
REM ============================================================
REM  SmartCat Web Server (FastAPI + Uvicorn)
REM  Port 8083
REM ============================================================

cd /d G:\Proj\SmartCat
call .venv\Scripts\activate

echo ============================================================
echo  SmartCat Web Server
echo  URL: http://0.0.0.0:8083
echo ============================================================
echo.

set PYTHONPATH=src
python -m uvicorn smartcat.api.app:app --host 0.0.0.0 --port 8083 --reload
