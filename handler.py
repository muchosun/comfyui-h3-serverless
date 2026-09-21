#!/usr/bin/env python3
"""
RunPod Serverless handler for MiniMax H3 (ComfyUI).
IDENTICAL request contract to the lustai i2v/Wan serverless:

  { "input": {
      "workflow": { ...ComfyUI API-format graph (required)... },
      "images":  [ { "name": "input_image.png", "image": "<base64 or data-URI or http(s) url>" } ]
  } }

Response:
  { "video_base64": "...", "filename": "...", "mime": "video/mp4" }   # if <= I2V_MAX_INLINE_MB
  or
  { "video_path": "/workspace/ComfyUI/output/...", "filename": "...", "too_large_mb": N }
Env: COMFY_HOST(127.0.0.1:8188), I2V_POLL_TIMEOUT_S(600), I2V_MAX_INLINE_MB(25),
     I2V_CLEANUP(1), COMFY_BOOT_TIMEOUT_S(300)
"""
import os, io, time, json, uuid, base64, urllib.request, urllib.error
import runpod

COMFY = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
POLL_TIMEOUT = float(os.environ.get("I2V_POLL_TIMEOUT_S", "600"))
MAX_INLINE_MB = float(os.environ.get("I2V_MAX_INLINE_MB", "25"))
CLEANUP = os.environ.get("I2V_CLEANUP", "1") not in ("0", "false", "False", "")
BOOT_TIMEOUT = float(os.environ.get("COMFY_BOOT_TIMEOUT_S", "300"))
INPUT_DIR = os.environ.get("COMFY_INPUT_DIR", "/workspace/ComfyUI/input")
OUTPUT_DIR = os.environ.get("COMFY_OUTPUT_DIR", "/workspace/ComfyUI/output")


class WorkerError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code}: {msg}")
        self.code = code; self.msg = msg


def _http(method, path, body=None, timeout=30):
    url = f"http://{COMFY}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _wait_for_comfy():
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        try:
            _http("GET", "/system_stats", timeout=5); return
        except Exception:
            time.sleep(2)
    raise WorkerError("comfy_boot", f"ComfyUI not ready after {BOOT_TIMEOUT}s")


def _decode_image(spec):
    """spec: {name, image(base64|data-uri|url)} -> (name, bytes)"""
    if not isinstance(spec, dict):
        raise WorkerError("bad_input", "input.images[] must be objects")
    name = spec.get("name") or f"input_{uuid.uuid4().hex[:8]}.png"
    src = spec.get("image") or spec.get("url") or spec.get("data")
    if not src:
        raise WorkerError("bad_input", "input.images[].image (base64/url) required")
    if isinstance(src, str) and src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=60) as r:
            return name, r.read()
    if isinstance(src, str) and src.startswith("data:"):
        src = src.split(",", 1)[1]
    return name, base64.b64decode(src)


def _save_input(name, raw):
    os.makedirs(INPUT_DIR, exist_ok=True)
    path = os.path.join(INPUT_DIR, os.path.basename(name))
    with open(path, "wb") as f:
        f.write(raw)
    return os.path.basename(name)


def _submit(workflow, client_id, prompt_id):
    body = {"prompt": workflow, "client_id": client_id, "prompt_id": prompt_id}
    return _http("POST", "/prompt", body, timeout=60)


def _history(prompt_id):
    h = _http("GET", f"/history/{prompt_id}", timeout=15)
    return h.get(prompt_id)


def _collect_video(entry):
    for node_out in (entry.get("outputs") or {}).values():
        for key in ("gifs", "videos", "video", "images"):
            for item in node_out.get(key, []) or []:
                fn = item.get("filename", "")
                if fn.lower().endswith((".mp4", ".webm", ".mkv", ".gif")):
                    sub = item.get("subfolder", "")
                    return os.path.join(OUTPUT_DIR, sub, fn), fn
    return None, None


def handler(job):
    ji = job.get("input") or {}
    workflow = ji.get("workflow")
    if not isinstance(workflow, dict) or not workflow:
        raise WorkerError("bad_input", "input.workflow is required (ComfyUI API-format graph)")
    _wait_for_comfy()

    for spec in ji.get("images") or []:
        name, raw = _decode_image(spec)
        _save_input(name, raw)

    client_id = uuid.uuid4().hex
    prompt_id = job.get("id") or uuid.uuid4().hex
    res = _submit(workflow, client_id, prompt_id)
    pid = res.get("prompt_id", prompt_id)
    if res.get("node_errors"):
        raise WorkerError("graph_error", json.dumps(res["node_errors"])[:500])

    deadline = time.time() + POLL_TIMEOUT
    entry = None
    while time.time() < deadline:
        entry = _history(pid)
        if entry and entry.get("status", {}).get("completed"):
            break
        if entry and entry.get("status", {}).get("status_str") == "error":
            msgs = entry.get("status", {}).get("messages", [])
            raise WorkerError("exec_error", json.dumps(msgs)[:600])
        time.sleep(2)
    else:
        raise WorkerError("timeout", f"no result within {POLL_TIMEOUT}s")

    path, fn = _collect_video(entry)
    if not path or not os.path.exists(path):
        raise WorkerError("no_output", "no video output produced")

    size_mb = os.path.getsize(path) / (1024 * 1024)
    out = {"filename": fn, "mime": "video/mp4", "size_mb": round(size_mb, 2)}
    if size_mb <= MAX_INLINE_MB:
        with open(path, "rb") as f:
            out["video_base64"] = base64.b64encode(f.read()).decode()
    else:
        out["video_path"] = path
        out["too_large_mb"] = round(size_mb, 2)
    if CLEANUP:
        try: os.remove(path)
        except Exception: pass
    return out


runpod.serverless.start({"handler": handler})
