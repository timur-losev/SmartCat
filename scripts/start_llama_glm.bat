@echo off
chcp 65001 >nul
REM ============================================================
REM  llama-server for SmartCat RAG agent
REM  GLM-4.7-Flash (MoE 30B-A3B) Q4_K_M - fully on GPU
REM  Port 8080
REM ============================================================
set MODEL=g:\Proj\Agents1\Models\GLM-4.7-Flash-UD-Q5_K_XL.gguf

echo ============================================================
echo  llama-server (GLM-4.7-Flash MoE Q5_K_XL)
echo  URL:   http://127.0.0.1:8080
echo  GPU layers: 48 (~21GB model + 45K ctx KV cache)
echo ============================================================
echo.

g:\Proj\Agents1\llama-cpp\llama-server.exe ^
  --model "%MODEL%" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --n-gpu-layers 48 ^
  --ctx-size 45056 ^
  --threads 16 ^
  --alias glm-flash
