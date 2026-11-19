"""Setting up the private mode's local model: is it there, where it runs, download it.

The private mode (profiles.yaml) runs a model through an Ollama server. The
server is the "ollama" service of docker compose, or Ollama installed on the
host (OLLAMA_HOST). This module reports what the server has and downloads the
model on request; it never installs anything on the host.
"""

import json
import threading
import urllib.request

import catalogue
from extractor.pipeline import load_engine

PRIVATE = "private"

_lock = threading.Lock()
_download = {"model": None, "status": "idle", "completed": 0, "total": 0, "error": None}


def private_model() -> str:
    """The model the private mode's engine runs (not its fallback)."""

    method = catalogue.load_profiles()["profiles"][PRIVATE]["method"]

    return load_engine(method)["fields_options"]["model"]


def on_gpu(host: str) -> bool | None:
    """Whether the loaded model sits in GPU memory; None when none is loaded."""

    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=1.5) as response:
            loaded = json.load(response).get("models", [])
    except (OSError, ValueError):
        return None
    if not loaded:
        return None

    return any(model.get("size_vram", 0) > 0 for model in loaded)


def status() -> dict:
    host = catalogue.ollama_host()
    models = catalogue.ollama_models()
    model = private_model()
    running = models is not None

    return {
        "host": host,
        "running": running,
        "model": model,
        "downloaded": running and catalogue.has_model(models, model),
        # Known only once the model has been used: Ollama unloads it after a few idle minutes.
        "on_gpu": on_gpu(host) if running else None,
        "download": download_state(),
    }


def download_state() -> dict:
    with _lock:
        return dict(_download)


def start_download(model: str) -> dict:
    """Start downloading the model in the background, unless a download is running."""

    with _lock:
        if _download["status"] == "running":
            return dict(_download)
        _download.update(model=model, status="running", completed=0, total=0, error=None)

    threading.Thread(target=_pull, args=(model,), daemon=True).start()

    return download_state()


def _pull(model: str) -> None:
    """Ask Ollama to pull the model and keep the bytes done across its layers."""

    layers: dict[str, tuple[int, int]] = {}
    request = urllib.request.Request(
        f"{catalogue.ollama_host()}/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw in response:
                if not raw.strip():
                    continue
                part = json.loads(raw)
                if "error" in part:
                    raise RuntimeError(part["error"])
                if part.get("digest") and part.get("total"):
                    layers[part["digest"]] = (part.get("completed", 0), part["total"])
                    with _lock:
                        _download["completed"] = sum(done for done, _ in layers.values())
                        _download["total"] = sum(size for _, size in layers.values())
                if part.get("status") == "success":
                    with _lock:
                        _download["status"] = "done"
                        _download["completed"] = _download["total"]
        if download_state()["status"] != "done":
            raise RuntimeError("Ollama ended the download without success")
    except (OSError, ValueError, RuntimeError) as error:
        with _lock:
            _download["status"] = "failed"
            _download["error"] = str(error)
    finally:
        catalogue.forget_ollama()
