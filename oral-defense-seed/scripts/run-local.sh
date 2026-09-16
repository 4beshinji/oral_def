#!/usr/bin/env bash
# This machine's existing ROCm/Qwen installation; override paths for another PC.
set -euo pipefail
oral_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
oral_server="${ORAL_LLAMA_SERVER:-$HOME/code/agent/local/chat/runtime/llama.cpp/build-rocm/bin/llama-server}"
oral_model="${ORAL_LLM_MODEL:-$HOME/code/agent/local/chat/models/qwen38/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf}"
oral_port="${ORAL_LLM_PORT:-10000}"
oral_logs="${ORAL_LOG_DIR:-$oral_root/.cache/local-runtime}"
[[ -x "$oral_server" && -f "$oral_model" && -x "$oral_root/.venv/bin/python" ]] || {
  echo 'LLM binary/model or .venv is missing. See docs/LOCAL_VALIDATION.md.' >&2; exit 1;
}
[[ -f "$oral_root/frontend/dist/index.html" ]] || {
  echo 'Build the UI first: npm --prefix frontend run build' >&2; exit 1;
}
"$oral_root/.venv/bin/python" - "$oral_port" <<'PY'
import socket, sys
for port in (int(sys.argv[1]), 8000):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            sys.exit(f'localhost:{port} is already in use. No process was stopped.')
PY
mkdir -p "$oral_logs"
oral_llm_pid=''
oral_app_pid=''
cleanup() {
  trap - EXIT INT TERM
  [[ -z "$oral_app_pid" ]] || kill "$oral_app_pid" 2>/dev/null || true
  [[ -z "$oral_llm_pid" ]] || kill "$oral_llm_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
LD_LIBRARY_PATH="$(dirname "$oral_server"):/opt/rocm/lib:/opt/rocm/lib/llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
ROCR_VISIBLE_DEVICES="${ORAL_GPU:-0}" \
"$oral_server" -m "$oral_model" --alias qwen38-27b-abliterated \
  --host 127.0.0.1 --port "$oral_port" --ctx-size 8192 --n-gpu-layers all \
  --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --parallel 1 \
  --fit on --reasoning off --spec-type draft-mtp --spec-draft-n-max 2 \
  > "$oral_logs/llama-server.log" 2>&1 &
oral_llm_pid=$!
"$oral_root/.venv/bin/python" - "$oral_port" "$oral_llm_pid" <<'PY'
import os, sys, time, urllib.request
port, pid = sys.argv[1], int(sys.argv[2])
for attempt in range(60):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        sys.exit('Local LLM exited. Check llama-server.log.')
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(1)
else:
    sys.exit('Local LLM did not become ready in 60 seconds. Check llama-server.log.')
PY
cd "$oral_root"
TEXT_PROVIDER=compatible TEXT_BASE_URL="http://127.0.0.1:$oral_port/v1" \
TEXT_MODEL=qwen38-27b-abliterated TEXT_RESPONSE_FORMAT=json_schema TEXT_API_KEY= \
TTS_PROVIDER=local MODEL_CATALOG_REFRESH_HOURS=0 \
DATA_DIR="${ORAL_DATA_DIR:-$oral_root/data/local-qwen}" \
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 \
  > "$oral_logs/app.log" 2>&1 &
oral_app_pid=$!
echo "Oral Defense: http://127.0.0.1:8000 (Ctrl+C stops the app and LLM)"
echo "Logs: $oral_logs"
wait -n "$oral_llm_pid" "$oral_app_pid"
