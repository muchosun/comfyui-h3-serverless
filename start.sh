#!/usr/bin/env bash
# MiniMax H3 serverless entrypoint. Mirrors the proven bootstrap:
# run the base image's /start_script.sh (model-path setup + per-env downloads +
# ComfyUI launch on 8188) in the background, then hand off to the RunPod handler.
export PYTHONUNBUFFERED=1
export PYTHONPATH=/opt/rp/vendor:${PYTHONPATH:-}
LOG=/workspace/comfy_serverless.log
mkdir -p /workspace 2>/dev/null || true
python3 -c 'import runpod' 2>/dev/null || python3 -m pip install -q runpod 2>/dev/null || true

if [ -f /start_script.sh ]; then
  echo "[start] launching base /start_script.sh in background" | tee -a "$LOG"
  ( /start_script.sh >> "$LOG" 2>&1 ) &
else
  echo "[start] /start_script.sh missing, launching ComfyUI directly" | tee -a "$LOG"
  CDIR=""
  for d in /ComfyUI /workspace/ComfyUI /comfyui; do [ -f "$d/main.py" ] && CDIR="$d" && break; done
  [ -z "$CDIR" ] && { echo "[start] FATAL: ComfyUI not found" | tee -a "$LOG"; exit 1; }
  EMP=""; [ -f "$CDIR/extra_model_paths.yaml" ] && EMP="--extra-model-paths-config $CDIR/extra_model_paths.yaml"
  ( cd "$CDIR" && python3 -u main.py --listen 127.0.0.1 --port 8188 --use-sage-attention $EMP --disable-dynamic-vram >> "$LOG" 2>&1 ) &
fi
echo "[start] handing off to handler (pid of comfy init: $!)" | tee -a "$LOG"
exec python3 -u /opt/rp/handler.py
