@echo off
chcp 65001 >nul
REM ============================================================
REM  llama-server for SmartCat RAG agent
REM  GLM-4.7-Flash (MoE 30B-A3B) Q4_K_M - fully on GPU
REM  Port 8080
REM ============================================================
set MODEL=g:\Proj\Agents1\Models\GLM\GLM-4.7-Flash-Q4_K_M.gguf

if not exist "%MODEL%" (
    echo Model not found: %MODEL%
    echo.
    echo Download with:
    echo   conda run -n hfh huggingface-cli download unsloth/GLM-4.7-Flash-GGUF GLM-4.7-Flash-Q4_K_M.gguf --local-dir g:\Proj\Agents1\Models\GLM\
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo  llama-server (GLM-4.7-Flash MoE Q4_K_M)
echo  URL:   http://127.0.0.1:8080
echo  GPU layers: 99 (all, ~18GB)
echo ============================================================
echo.

g:\Proj\Agents1\llama-cpp\llama-server.exe ^
  --model "%MODEL%" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --n-gpu-layers 99 ^
  --ctx-size 32768 ^
  --threads 16 ^
  --alias glm-flash
