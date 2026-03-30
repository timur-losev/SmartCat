@echo off
REM ============================================================
REM  llama-server for QA extraction
REM  Qwen3 14B Q8_0 — fully on GPU (15GB VRAM)
REM  Port 8080
REM ============================================================
set MODEL=g:\Proj\Agents1\Models\Qwen\Qwen3-14B-Q8_0.gguf

echo ============================================================
echo  llama-server (Qwen3 14B Q8 - QA extraction)
echo  Model: %MODEL%
echo  URL:   http://127.0.0.1:8080
echo  GPU layers: 99 (all)
echo ============================================================
echo.

g:\Proj\Agents1\llama-cpp\llama-server.exe ^
  --model "%MODEL%" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --n-gpu-layers 99 ^
  --ctx-size 16384 ^
  --threads 8 ^
  --alias qwen3-14b
