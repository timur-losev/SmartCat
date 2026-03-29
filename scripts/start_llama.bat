@echo off
REM ============================================================
REM  llama-server for SmartCat RAG agent
REM  Qwen3 32B Q8_0 — 33 GPU layers, rest on CPU
REM  Port 8080 (default for SmartCat agent)
REM ============================================================
set MODEL=g:\Proj\Agents1\Models\Qwen\Qwen3-32B-Q8_0.gguf

echo ============================================================
echo  llama-server (SmartCat RAG)
echo  Model: %MODEL%
echo  URL:   http://127.0.0.1:8080
echo  GPU layers: 33
echo ============================================================
echo.

g:\Proj\Agents1\llama-cpp\llama-server.exe ^
  --model "%MODEL%" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --n-gpu-layers 33 ^
  --ctx-size 32768 ^
  --threads 16 ^
  --alias qwen3-32b
