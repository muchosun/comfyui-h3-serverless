#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
export PYTHONPATH=/opt/rp/vendor:${PYTHONPATH:-}
LOG=/workspace/comfy_serverless.log
# locate ComfyUI
CDIR=""
for d in /ComfyUI /workspace/ComfyUI /comfyui; do
  [ -f "$d/main.py" ] && CDIR="$d" && break
done
echo "[start] ComfyUI dir: ${CDIR:-NOT FOUND}" | tee -a "$LOG"
[ -z "$CDIR" ] && { echo "[start] FATAL: ComfyUI not found" | tee -a "$LOG"; exit 1; }
EMP=""
[ -f "$CDIR/extra_model_paths.yaml" ] && EMP="--extra-model-paths-config $CDIR/extra_model_paths.yaml"
# launch ComfyUI in background (models come from mounted network volume)
( cd "$CDIR" && python3 -u main.py --listen 127.0.0.1 --port 8188 --use-sage-attention $EMP --disable-dynamic-vram >> "$LOG" 2>&1 ) &
echo "[start] ComfyUI launching (pid $!), handing off to handler" | tee -a "$LOG"
python3 -c 'import runpod' 2>/dev/null || python3 -m pip install -q runpod 2>/dev/null || true
# hand off to serverless handler (it polls 127.0.0.1:8188 up to COMFY_BOOT_TIMEOUT_S)
exec python3 -u /opt/rp/handler.py
