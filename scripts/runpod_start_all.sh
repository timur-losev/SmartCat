#!/bin/bash
# SmartCat — Start all services with watchdog
# Usage: /workspace/start_all.sh

export PATH=/usr/local/cuda/bin:/workspace/llama-cpp/bin:$PATH
export PYTHONPATH=/workspace/repo/src
export LD_LIBRARY_PATH=/workspace/llama.cpp-src/build/src:/usr/local/cuda/lib64:$LD_LIBRARY_PATH
ln -sf /workspace/repo/web /web 2>/dev/null

LOGDIR=/workspace/logs
mkdir -p $LOGDIR

# --- Stop existing ---
echo '=== Stopping existing services ==='
pkill -f qdrant 2>/dev/null
pkill -f llama-server 2>/dev/null
pkill -f uvicorn 2>/dev/null
pkill -f watchdog.sh 2>/dev/null
sleep 2

# --- 1. Qdrant ---
echo '=== Starting Qdrant ==='
cd /workspace && nohup ./qdrant --config-path qdrant_config/config.yaml > $LOGDIR/qdrant.log 2>&1 &
echo "Qdrant PID: $!"
sleep 2 && curl -s http://localhost:6333/healthz && echo ' OK'

# --- 2. LLM (llama-server with verbose logging) ---
echo '=== Starting LLM (Gemma 4 31B) ==='
nohup /workspace/llama-cpp/bin/llama-server \
    -m /workspace/models/gemma-4-31B-it-UD-Q5_K_XL.gguf \
    -np 1 -ngl 99 \
    --host 0.0.0.0 --port 8080 \
    -c 65536 \
    --no-context-shift \
    --alias gemma-4-31b \
    --log-timestamps \
    --log-prefix \
    --log-verbosity 3 \
    --log-file $LOGDIR/llama.log \
    > $LOGDIR/llama-stdout.log 2>&1 &
LLM_PID=$!
echo "LLM PID: $LLM_PID (loading ~20s...)"

# --- 3. FastAPI ---
echo '=== Starting FastAPI ==='
nohup python3 -m uvicorn smartcat.api.app:app \
    --host 0.0.0.0 --port 8083 \
    > $LOGDIR/fastapi.log 2>&1 &
echo "FastAPI PID: $!"

# --- 4. Watchdog ---
echo '=== Starting Watchdog ==='
cat > /workspace/watchdog.sh << 'WATCHDOG'
#!/bin/bash
# Watchdog — restarts crashed services
export PATH=/usr/local/cuda/bin:/workspace/llama-cpp/bin:$PATH
export PYTHONPATH=/workspace/repo/src
export LD_LIBRARY_PATH=/workspace/llama.cpp-src/build/src:/usr/local/cuda/lib64:$LD_LIBRARY_PATH
LOGDIR=/workspace/logs

while true; do
    sleep 15

    # Check LLM
    if ! curl -sf http://localhost:8080/health > /dev/null 2>&1; then
        TS=$(date '+%Y-%m-%d %H:%M:%S')
        echo "[$TS] LLM DOWN — restarting" >> $LOGDIR/watchdog.log

        # Save crash info
        echo "=== CRASH at $TS ===" >> $LOGDIR/llama-crashes.log
        echo "--- GPU state ---" >> $LOGDIR/llama-crashes.log
        nvidia-smi >> $LOGDIR/llama-crashes.log 2>&1
        echo "--- Last 30 lines of llama log ---" >> $LOGDIR/llama-crashes.log
        tail -30 $LOGDIR/llama.log >> $LOGDIR/llama-crashes.log 2>&1
        tail -30 $LOGDIR/llama-stdout.log >> $LOGDIR/llama-crashes.log 2>&1
        echo "--- dmesg (OOM?) ---" >> $LOGDIR/llama-crashes.log
        dmesg | tail -10 >> $LOGDIR/llama-crashes.log 2>&1
        echo "" >> $LOGDIR/llama-crashes.log

        # Kill zombie
        pkill -9 -f llama-server 2>/dev/null
        sleep 3

        # Restart
        nohup /workspace/llama-cpp/bin/llama-server \
            -m /workspace/models/gemma-4-31B-it-UD-Q5_K_XL.gguf \
            -np 1 -ngl 99 \
            --host 0.0.0.0 --port 8080 \
            -c 65536 \
            --no-context-shift \
            --alias gemma-4-31b \
            --log-timestamps \
            --log-prefix \
            --log-verbosity 3 \
            --log-file $LOGDIR/llama.log \
            > $LOGDIR/llama-stdout.log 2>&1 &
        echo "[$TS] LLM restarted, PID: $!" >> $LOGDIR/watchdog.log

        sleep 30  # wait for model load
    fi

    # Check Qdrant
    if ! curl -sf http://localhost:6333/healthz > /dev/null 2>&1; then
        TS=$(date '+%Y-%m-%d %H:%M:%S')
        echo "[$TS] Qdrant DOWN — restarting" >> $LOGDIR/watchdog.log
        pkill -f qdrant 2>/dev/null; sleep 2
        cd /workspace && nohup ./qdrant --config-path qdrant_config/config.yaml > $LOGDIR/qdrant.log 2>&1 &
        echo "[$TS] Qdrant restarted, PID: $!" >> $LOGDIR/watchdog.log
        sleep 5
    fi

    # Check FastAPI
    if ! curl -sf http://localhost:8083/api/health > /dev/null 2>&1; then
        TS=$(date '+%Y-%m-%d %H:%M:%S')
        echo "[$TS] FastAPI DOWN — restarting" >> $LOGDIR/watchdog.log
        pkill -f uvicorn 2>/dev/null; sleep 2
        nohup python3 -m uvicorn smartcat.api.app:app \
            --host 0.0.0.0 --port 8083 \
            > $LOGDIR/fastapi.log 2>&1 &
        echo "[$TS] FastAPI restarted, PID: $!" >> $LOGDIR/watchdog.log
        sleep 5
    fi
done
WATCHDOG
chmod +x /workspace/watchdog.sh
nohup /workspace/watchdog.sh > /dev/null 2>&1 &
echo "Watchdog PID: $!"

# --- Wait for LLM ---
echo ''
echo '=== Waiting for LLM to load ==='
for i in $(seq 1 30); do
    if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
        echo "LLM ready! (${i}s)"
        break
    fi
    sleep 1
done

# --- Summary ---
echo ''
echo '========================================'
echo '  All services launched'
echo '========================================'
echo "  Qdrant:   $(curl -sf http://localhost:6333/healthz 2>/dev/null && echo 'OK' || echo 'STARTING')"
echo "  LLM:      $(curl -sf http://localhost:8080/health 2>/dev/null && echo 'OK' || echo 'LOADING')"
echo "  FastAPI:  $(curl -sf http://localhost:8083/api/health 2>/dev/null && echo 'OK' || echo 'STARTING')"
echo "  Watchdog: running (checks every 15s)"
echo ''
echo "  Logs:     $LOGDIR/"
echo "    llama.log          — LLM verbose log"
echo "    llama-stdout.log   — LLM stdout/stderr"
echo "    llama-crashes.log  — crash dumps"
echo "    fastapi.log        — API server"
echo "    qdrant.log         — vector DB"
echo "    watchdog.log       — restart events"
echo '========================================'
