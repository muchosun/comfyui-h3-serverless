#!/usr/bin/env bash
# MiniMax H3 serverless entrypoint — LEAN direct launch.
# The base image bakes ComfyUI + custom nodes at /ComfyUI and a venv at /opt/venv.
# We do NOT run the base /start_script.sh (it git-clones from GitHub every boot and
# re-downloads ~67GB of models when it can't find the volume at /workspace — on
# serverless the network volume is mounted at /runpod-volume, so that path check
# fails and it times out). Instead: point ComfyUI at the volume's models and launch.
export PYTHONUNBUFFERED=1
COMFY=/ComfyUI
PY=/opt/venv/bin/python
LOG=/tmp/comfy_serverless.log
[ -x "$PY" ] || PY=python3

# Locate the network volume (serverless: /runpod-volume; pod fallback: /workspace)
VOL=""
for v in /runpod-volume /workspace; do
  [ -d "$v/ComfyUI/models" ] && VOL="$v" && break
done
if [ -n "$VOL" ]; then
  for sub in models input output user; do
    if [ -d "$VOL/ComfyUI/$sub" ]; then
      [ -L "$COMFY/$sub" ] || rm -rf "$COMFY/$sub" 2>/dev/null || true
      ln -sfn "$VOL/ComfyUI/$sub" "$COMFY/$sub"
    fi
  done
  mkdir -p "$COMFY/input" "$COMFY/output" 2>/dev/null || true
  echo "[start] linked models/input/output/user from $VOL/ComfyUI" | tee -a "$LOG"
else
  echo "[start] WARNING: no volume with ComfyUI/models found (looked in /runpod-volume,/workspace)" | tee -a "$LOG"
fi

# Launch ComfyUI directly, CLEAN env (no vendor on PYTHONPATH -> no dep shadowing)
( cd "$COMFY" && env -u PYTHONPATH "$PY" -u main.py --listen 127.0.0.1 --port 8188 \
    --use-sage-attention --disable-auto-launch --disable-dynamic-vram >> "$LOG" 2>&1 ) &
echo "[start] ComfyUI launching (pid $!) from $COMFY via $PY" | tee -a "$LOG"

# runpod SDK for the handler only: vendored dir on PYTHONPATH for THIS process,
# with a venv-pip fallback (does not touch the ComfyUI process).
"$PY" -c 'import runpod' 2>/dev/null || PYTHONPATH=/opt/rp/vendor "$PY" -c 'import runpod' 2>/dev/null \
  || "$PY" -m pip install -q runpod 2>/dev/null || true
exec env PYTHONPATH="/opt/rp/vendor:${PYTHONPATH:-}" "$PY" -u /opt/rp/handler.py
