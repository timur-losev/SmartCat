@echo off
chcp 65001 >nul
REM ============================================================
REM  llama-server for SmartCat RAG agent
REM  Qwen3.5 35B-A3B (MoE) Q5_K_M - fully on GPU
REM  Port 8080
REM ============================================================
set MODEL=g:\Proj\Agents1\Models\Qwen\Q5_K_M\Qwen3.5-35B-A3B-Q5_K_M-00001-of-00002.gguf

echo ============================================================
echo  llama-server (Qwen3.5 35B-A3B MoE Q5_K_M)
echo  URL:   http://127.0.0.1:8080
echo  GPU layers: 99 (all)
echo ============================================================
echo.

g:\Proj\Agents1\llama-cpp\llama-server.exe ^
  --model "%MODEL%" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --n-gpu-layers 99 ^
  --ctx-size 32768 ^
  --threads 16 ^
  --alias qwen35-moe
