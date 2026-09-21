# MiniMax H3 — RunPod Serverless (identical request contract to lustai i2v/Wan)

Same request envelope as your existing `lustai-i2v-5090-is2` endpoint, so callers switch
between Wan and MiniMax H3 by only swapping `input.workflow`.

## Request contract (identical to Wan endpoint)
POST https://api.runpod.ai/v2/<ENDPOINT_ID>/runsync
```json
{ "input": {
    "workflow": { ...ComfyUI API-format graph... },
    "images":  [ { "name": "input_image.png", "image": "<base64 | data-uri | https url>" } ]
} }
```
- `input.workflow` (required): ComfyUI API graph. For H3 use `h3_autores_workflow_api.json`
  (auto-reads input resolution from the photo → generates in its aspect → 2x latent upscale,
  MysticXXX @1.0 + turbo 8-step). The graph's LoadImage name is `input_image.png`.
- `input.images[]`: the source frame(s). `name` must match the LoadImage value in the workflow.
- Response: `{ "video_base64", "filename", "mime":"video/mp4", "size_mb" }` when ≤ I2V_MAX_INLINE_MB,
  else `{ "video_path", "too_large_mb" }`.

## 1. Build & push  (needs YOUR registry login — that's why you run this step)
```bash
cd h3-serverless-pkg
docker build -t <your-registry>/comfyui-h3-serverless:v1 .
docker login                       # your DockerHub/registry creds
docker push <your-registry>/comfyui-h3-serverless:v1
```
Image base = `hearmeman/comfyui-minimax-template:v9` (has MiniMaxH3 nodes + comfy_kitchen).
Models are NOT baked in — they load from the network volume at runtime.

## 2. Create the serverless endpoint (RunPod console or API)
- Template: image = `<your-registry>/comfyui-h3-serverless:v1`, container disk ~40GB.
  Env (defaults already in image): I2V_POLL_TIMEOUT_S=600, I2V_MAX_INLINE_MB=25, I2V_CLEANUP=1, COMFY_BOOT_TIMEOUT_S=300.
- Endpoint: GPU `ADA_32_PRO` (RTX 5090, 32GB) — same class as your Wan endpoint.
  Attach **network volume `uocae6ided`** (EU-RO-1) — it already holds the H3 models we downloaded.
  - `workersMin=1` → always-warm (instant, models resident, bills continuously) — matches "server lies ready".
  - `workersMin=0` → scale-to-zero (cheap; ~30-60s cold start to load models from the volume).

## 3. Call it (same shape as Wan)
```bash
python3 - <<'PY'
import json,base64,urllib.request
wf=json.load(open("h3_autores_workflow_api.json"))
img=base64.b64encode(open("your_image.png","rb").read()).decode()
body={"input":{"workflow":wf,"images":[{"name":"input_image.png","image":img}]}}
req=urllib.request.Request("https://api.runpod.ai/v2/<ENDPOINT_ID>/runsync",
    data=json.dumps(body).encode(),
    headers={"Authorization":"Bearer <RUNPOD_API_KEY>","Content-Type":"application/json"})
r=json.load(urllib.request.urlopen(req,timeout=900))
out=r["output"]
open("out.mp4","wb").write(base64.b64decode(out["video_base64"])) if "video_base64" in out else print(out)
PY
```

## Integration note (verify once)
The Hearmeman base serves ComfyUI from `/ComfyUI` and expects models on the mounted volume via
`/ComfyUI/extra_model_paths.yaml`. If ComfyUI in the worker doesn't see the volume models,
adjust `start.sh` model-paths (or symlink `/ComfyUI/models` → `/workspace/ComfyUI/models`).
This is the one thing to confirm on the first worker boot (check /workspace/comfy_serverless.log).
