# MiniMax H3 (I2V) — Serverless Integration Guide

A RunPod Serverless endpoint that turns an input image into a MiniMax‑H3 video
(ComfyUI under the hood). **The request contract is identical to the existing
WAN i2v endpoint** — to switch WAN ↔ H3 you change only `input.workflow`
(and the input image).

---

## 1. Endpoint

| | |
|---|---|
| Endpoint ID | `p5a1eli13sgzb5` |
| Base URL | `https://api.runpod.ai/v2/p5a1eli13sgzb5` |
| Region / GPU | EU‑RO‑1 · A100‑80GB (preferred) → RTX 5090 (fallback) |
| Auth | `Authorization: Bearer <RUNPOD_API_KEY>` |
| WAN endpoint (for reference) | `9sdyt8e6xcxt0k` — same contract |

> **API key:** use a RunPod API key from the account that owns this endpoint
> (RunPod console → Settings → API Keys). It is a secret — it is **not** in this
> repo; get it from the endpoint owner through a secure channel and store it in
> your own env/secrets, never in code.

### Routes (standard RunPod Serverless)
- `POST /run` — submit async → `{ "id": "...", "status": "IN_QUEUE" }`
- `POST /runsync` — submit and block until done (fine for short clips)
- `GET  /status/{id}` — poll one job
- `POST /cancel/{id}` — cancel
- `GET  /health` — worker/queue counts

---

## 2. Request

```json
{
  "input": {
    "workflow": { "...": "a ComfyUI API-format graph (required)" },
    "images": [
      { "name": "input_image.png", "image": "<base64 | data-URI | http(s) URL>" }
    ]
  }
}
```

- `input.workflow` — **required.** A ComfyUI **API‑format** graph (the `/prompt`
  shape: `{ "<nodeId>": { "class_type": ..., "inputs": {...} }, ... }`), **not**
  the UI "save" format. Use `example_request.json` / `h3_autores_workflow_api.json`
  in this repo as the starting graph.
- `input.images[].name` — **must match** the filename referenced by the
  `LoadImage` node in the workflow. In the bundled graph that is `input_image.png`
  (node id `image`).
- `input.images[].image` — base64, `data:` URI, or an `http(s)` URL. The worker
  hands it to ComfyUI's own `/upload/image`, so it lands exactly where the graph
  expects it.

Multiple images are supported (one object per input the graph consumes).

## 3. Response

```json
{ "filename": "..._00003_.mp4", "mime": "video/mp4", "size_mb": 3.16,
  "video_base64": "<...>" }          // inline when size_mb <= 25
```
or, for larger outputs:
```json
{ "filename": "...mp4", "mime": "video/mp4", "size_mb": 40.0,
  "video_path": "/ComfyUI/output/...mp4", "too_large_mb": 40.0 }
```
> Inline cutoff is env `I2V_MAX_INLINE_MB` (currently **25 MB**). A `video_path`
> lives on the ephemeral worker and is **not** retrievable by the caller — for
> big outputs raise the cutoff or add an S3/bucket upload in `handler.py`
> (`_collect_video`). Typical 5 s clips are a few MB → inline is fine.

## 4. The bundled H3 workflow (`h3_autores_workflow_api.json`)

Auto‑resolution I2V: reads the input image's size and generates in its aspect
ratio (long edge ~1152, snapped to /32), MysticXXX LoRA + turbo 8‑step, then a
2× latent upscale. Two fields you normally set per request:

| Node id | What to set |
|---|---|
| `cond` (`MiniMaxH3ImageToVideo`) | `inputs.prompt` — the text prompt |
| `image` (`LoadImage`) | `inputs.image` — keep `"input_image.png"` and send the image under that same `name` |

Models available on the attached network volume (already referenced by the graph):
- **Diffusion (UNET):** minimax_h3_fl2va_pruned_fp8_scaled.safetensors, minimax_h3_ref2va_pruned_fp8_scaled.safetensors
- **LoRAs:** MysticXXX_MMH3-V4.safetensors, minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors, minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors, minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors
- **Text encoder (CLIP, 32B — required by H3):** qwen3vl_32b_minimax_h3_int8_convrot.safetensors

## 5. Timing & scaling

- **Cold start** (first request after idle): ~4–5 min = image pull + ComfyUI boot,
  then generation. The endpoint scales to **0 workers when idle** (min=0), so
  expect a cold start after a quiet period.
- **Warm**: generation only. The bundled autores+2×‑upscale 5 s clip is ~6–7 min
  on A100‑80; smaller/no‑upscale variants are faster.
- Want instant responses? Ask the owner to set **workers min = 1** (keeps one GPU
  warm 24/7, at cost). Concurrency = workers max.

## 6. Errors

A failed job returns RunPod `status: "FAILED"` with an `error` string. Handler
errors are `"<code>: <detail>"`, e.g.:

| code | meaning |
|---|---|
| `bad_input` | `input.workflow` missing / `images[]` malformed |
| `graph_error` / `comfy_http: … 400` | ComfyUI rejected the graph (bad node, missing model, wrong input filename) — detail includes ComfyUI's body |
| `exec_error` | a node raised during execution |
| `comfy_died` | ComfyUI crashed mid‑run (e.g. OOM) — detail includes the ComfyUI log tail |
| `comfy_boot` | ComfyUI didn't come up in time (detail: log + model‑dir listing) |
| `timeout` | no result within `I2V_POLL_TIMEOUT_S` (900 s) |
| `no_output` | graph finished but produced no video |

## 7. Examples

### curl (async + poll)
```bash
KEY=<RUNPOD_API_KEY>; EP=p5a1eli13sgzb5
# build body: {"input":{"workflow":<graph with prompt+image set>, "images":[{name,image}]}}
ID=$(curl -s -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
      -d @request.json https://api.runpod.ai/v2/$EP/run | jq -r .id)
while :; do
  S=$(curl -s -H "Authorization: Bearer $KEY" https://api.runpod.ai/v2/$EP/status/$ID)
  st=$(echo "$S" | jq -r .status); echo "$st"
  [ "$st" = COMPLETED ] && echo "$S" | jq -r .output.video_base64 | base64 -d > out.mp4 && break
  [ "$st" = FAILED ] && echo "$S" | jq -r .error && break
  sleep 10
done
```

### Python
```python
import base64, json, time, requests

EP = "p5a1eli13sgzb5"
BASE = f"https://api.runpod.ai/v2/{EP}"
H = {"Authorization": f"Bearer {RUNPOD_API_KEY}", "Content-Type": "application/json"}

workflow = json.load(open("h3_autores_workflow_api.json"))       # API-format graph
workflow["cond"]["inputs"]["prompt"] = "….your prompt…."
img_b64 = base64.b64encode(open("photo.jpg", "rb").read()).decode()

job = {"input": {"workflow": workflow,
                 "images": [{"name": "input_image.png", "image": img_b64}]}}
jid = requests.post(f"{BASE}/run", headers=H, json=job).json()["id"]

while True:
    s = requests.get(f"{BASE}/status/{jid}", headers=H).json()
    if s["status"] == "COMPLETED":
        o = s["output"]
        open(o["filename"], "wb").write(base64.b64decode(o["video_base64"]))
        break
    if s["status"] == "FAILED":
        raise RuntimeError(s.get("error"))
    time.sleep(10)
```

`example_request.json` in this repo is a full, ready‑to‑send body.

## 8. Maintenance (for whoever owns the image)

- Source of truth: this repo. `handler.py` (worker), `start.sh` (boot),
  `Dockerfile`, `.github/workflows/build-push.yml` (the crane build).
- Image: `docker.io/muchosun/comfyui-h3-serverless:v7` (public). Rebuild:
  bump the tag in the workflow, push, run `build-push`, then point the serverless
  **template `0dnakgvhf1`** at the new tag. See `BUILDER_SETUP.md`.
- The image bakes ComfyUI + custom nodes; **models live on the network volume**
  (`uocae6ided`, mounted at `/runpod-volume`), not in the image.
