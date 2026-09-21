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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            eb = e.read().decode(errors="replace")
        except Exception:
            eb = ""
        raise WorkerError("comfy_http", f"{method} {path} -> HTTP {e.code}: {eb[:1800]}")


def _boot_diag():
    """On boot failure, surface why: comfy log tail + model dir listings."""
    out = []
    for lp in ("/tmp/comfy_serverless.log", "/workspace/comfy_serverless.log"):
        if os.path.exists(lp):
            try:
                out.append(f"--- {lp} (tail) ---\n" + open(lp, errors="replace").read()[-3500:])
            except Exception as e:
                out.append(f"{lp}: {e}")
    for d in ("/runpod-volume/ComfyUI/models", "/workspace/ComfyUI/models",
              "/ComfyUI/models", "/runpod-volume", "/workspace"):
        try:
            out.append(f"--- ls {d} ---\n" + "\n".join(sorted(os.listdir(d))[:40]))
        except Exception as e:
            out.append(f"ls {d}: {e}")
    return ("\n".join(out))[:6500]


def _wait_for_comfy():
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        try:
            _http("GET", "/system_stats", timeout=5); return
        except Exception:
            time.sleep(2)
    raise WorkerError("comfy_boot", f"ComfyUI not ready after {BOOT_TIMEOUT}s\n{_boot_diag()}")


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
    """Hand the input to ComfyUI via its own /upload/image so it lands exactly
    where ComfyUI reads inputs, regardless of the on-disk input path / symlinks.
    Falls back to a direct disk write if the upload endpoint is unavailable."""
    bn = os.path.basename(name)
    boundary = "----h3rp" + uuid.uuid4().hex
    data = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
        f"filename=\"{bn}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode() + raw + b"\r\n" + (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"http://{COMFY}/upload/image", data=data, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        return bn
    except Exception:
        try:
            os.makedirs(INPUT_DIR, exist_ok=True)
            with open(os.path.join(INPUT_DIR, bn), "wb") as f:
                f.write(raw)
            return bn
        except Exception as e:
            raise WorkerError("input_save", f"could not provide input {bn}: {e}")


def _submit(workflow, client_id):
    # Do NOT pass prompt_id: newer ComfyUI validates it must be a canonical
    # hyphenated UUID, which the RunPod job id (e.g. "<uuid>-e2") is not.
    # Let ComfyUI mint one and read it back from the response.
    body = {"prompt": workflow, "client_id": client_id}
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


def _gpu():
    """GPU name/VRAM the worker is actually running on (from ComfyUI /system_stats)."""
    try:
        st = _http("GET", "/system_stats", timeout=10)
        dev = (st.get("devices") or [{}])[0]
        vram = dev.get("vram_total")
        return {"name": dev.get("name"), "vram_gb": round(vram / (1024**3), 1) if vram else None}
    except Exception as e:
        return {"error": str(e)[:120]}


def _timing(entry, workflow):
    """Per-node timing from ComfyUI history messages, to see where wall-time goes
    (model load lead-in vs KSampler vs upscaler vs VAE decode)."""
    msgs = (entry.get("status") or {}).get("messages") or []
    ev = []
    for m in msgs:
        if isinstance(m, list) and len(m) == 2 and isinstance(m[1], dict) and "timestamp" in m[1]:
            ev.append((m[1]["timestamp"], m[0], m[1].get("node")))
    ev.sort()
    if len(ev) < 2:
        return {}
    def cls(nid):
        try:
            return workflow.get(str(nid), {}).get("class_type", str(nid))
        except Exception:
            return str(nid)
    t0 = ev[0][0]
    gaps = []
    for i in range(1, len(ev)):
        dt = round((ev[i][0] - ev[i - 1][0]) / 1000.0, 1)
        node = ev[i][2] or ev[i - 1][2]
        gaps.append({"s": dt, "after_event": ev[i - 1][1], "node": cls(node)})
    gaps_sorted = sorted(gaps, key=lambda g: g["s"], reverse=True)[:8]
    return {"total_s": round((ev[-1][0] - t0) / 1000.0, 1),
            "events": len(ev),
            "lead_in_s": round((ev[1][0] - t0) / 1000.0, 1),
            "top_gaps": gaps_sorted}


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
    res = _submit(workflow, client_id)
    pid = res.get("prompt_id")
    if not pid:
        raise WorkerError("submit", f"ComfyUI returned no prompt_id: {json.dumps(res)[:400]}")
    if res.get("node_errors"):
        raise WorkerError("graph_error", json.dumps(res["node_errors"])[:500])

    deadline = time.time() + POLL_TIMEOUT
    entry = None
    conn_fails = 0
    while time.time() < deadline:
        try:
            entry = _history(pid)
            conn_fails = 0
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            # ComfyUI unreachable mid-run -> it likely crashed (OOM / CUDA error).
            # Retry a few times (it may be briefly busy), then surface its log.
            conn_fails += 1
            if conn_fails >= 5:
                raise WorkerError("comfy_died",
                    f"ComfyUI became unreachable mid-run ({e}); likely crashed.\n{_boot_diag()}")
            time.sleep(3)
            continue
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
    try:
        out["timing"] = _timing(entry, workflow)
    except Exception as e:
        out["timing"] = {"error": str(e)}
    out["gpu"] = _gpu()
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
