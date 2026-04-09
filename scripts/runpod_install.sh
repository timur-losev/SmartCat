#!/bin/bash
set -e
echo "========================================"
echo "  SmartCat — Full Install & Setup"
echo "========================================"

export PATH=/usr/local/cuda/bin:/workspace/llama-cpp/bin:$PATH
export CUDACXX=/usr/local/cuda/bin/nvcc
export PYTHONPATH=/workspace/repo/src
export LD_LIBRARY_PATH=/workspace/llama.cpp-src/build/src:/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# --- 1. Git pull latest code ---
echo ''
echo '=== [1/6] Updating code ==='
cd /workspace/repo && git pull
echo 'Code updated'

# --- 2. Python packages ---
echo ''
echo '=== [2/6] Installing Python packages ==='
pip install --root-user-action=ignore --break-system-packages -q \
    qdrant-client fastapi uvicorn httpx sentence-transformers \
    structlog tqdm click rich python-dateutil beautifulsoup4 \
    readability-lxml requests einops pymupdf langdetect \
    argostranslate 'mcp[cli]>=1.0'
echo 'Python packages OK'

# --- 3. llama-cpp-python with CUDA ---
echo ''
echo '=== [3/6] Checking llama-cpp-python CUDA ==='
if python3 -c 'from llama_cpp import llama_cpp; assert llama_cpp.llama_supports_gpu_offload()' 2>/dev/null; then
    echo 'llama-cpp-python already has CUDA, skipping build'
else
    echo 'Building llama-cpp-python with CUDA (takes ~3 min)...'
    CMAKE_ARGS="-DGGML_CUDA=on" pip install --root-user-action=ignore \
        --break-system-packages --force-reinstall --no-cache-dir llama-cpp-python
fi
echo 'llama-cpp-python OK'

# --- 4. Symlinks ---
echo ''
echo '=== [4/6] Setting up symlinks ==='
ln -sf /workspace/repo/src/smartcat /workspace/smartcat
ln -sf /workspace/repo/scripts /workspace/scripts
ln -sf /workspace/repo/web /workspace/web
ln -sf /workspace/repo/web /web
mkdir -p /workspace/repo/data
ln -sf /workspace/data/smartcat.db /workspace/repo/data/smartcat.db
echo 'Symlinks OK'

# --- 5. SSH config for GitHub ---
echo ''
echo '=== [5/6] Configuring SSH ==='
if [ -f /workspace/.ssh_github ]; then
    mkdir -p ~/.ssh
    cat > ~/.ssh/config << 'SSHEOF'
Host github.com
  IdentityFile /workspace/.ssh_github
  StrictHostKeyChecking no
SSHEOF
    chmod 600 ~/.ssh/config /workspace/.ssh_github
    echo 'GitHub SSH OK'
else
    echo 'No GitHub SSH key found, skipping'
fi

# --- 6. Verify ---
echo ''
echo '=== [6/6] Verification ==='
python3 << 'PYEOF'
import torch
print(f'  torch={torch.__version__} cuda={torch.cuda.is_available()}')
from llama_cpp import llama_cpp
print(f'  llama GPU={llama_cpp.llama_supports_gpu_offload()}')
from qdrant_client import QdrantClient
print('  qdrant-client OK')
from sentence_transformers import SentenceTransformer
print('  sentence-transformers OK')
from mcp.server.fastmcp import FastMCP
print('  MCP SDK OK')
PYEOF

echo ''
echo '=== Workspace ==='
du -sh /workspace/models/ /workspace/qdrant_storage/ /workspace/data/ 2>/dev/null | while read size path; do
    echo "  $path  $size"
done
df -h /workspace | tail -1 | awk '{print "  Disk: "$3"/"$2" used ("$5")"}'

echo ''
echo '========================================'
echo '  Install complete!'
echo '  Run: /workspace/start_all.sh'
echo '========================================'
