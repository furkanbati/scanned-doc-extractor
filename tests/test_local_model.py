import io
import json
import threading
import urllib.request

import pytest

import catalogue
import local_model


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(local_model, "_download",
                        {"model": None, "status": "idle", "completed": 0, "total": 0, "error": None})
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")


def stream(*parts):
    return io.BytesIO(b"".join(json.dumps(part).encode() + b"\n" for part in parts))


def test_the_download_adds_up_the_layers(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return stream(
            {"status": "pulling manifest"},
            {"status": "pulling a", "digest": "a", "total": 100, "completed": 40},
            {"status": "pulling b", "digest": "b", "total": 50, "completed": 50},
            {"status": "pulling a", "digest": "a", "total": 100, "completed": 100},
            {"status": "verifying sha256 digest"},
            {"status": "success"},
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    local_model._pull("qwen3-vl:2b-instruct")

    state = local_model.download_state()
    assert requests == [{"model": "qwen3-vl:2b-instruct", "stream": True}]
    assert (state["status"], state["completed"], state["total"]) == ("done", 150, 150)


def test_an_error_from_ollama_fails_the_download(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: stream({"error": "pull model manifest: file does not exist"}))

    local_model._pull("no-such-model")

    state = local_model.download_state()
    assert state["status"] == "failed"
    assert "does not exist" in state["error"]


def test_a_stream_that_ends_without_success_fails(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: stream({"status": "pulling manifest"}))

    local_model._pull("qwen3-vl:2b-instruct")

    assert local_model.download_state()["status"] == "failed"


def test_the_download_forgets_the_cached_model_list(monkeypatch):
    forgotten = []
    monkeypatch.setattr(catalogue, "forget_ollama", lambda: forgotten.append(True))
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: stream({"status": "success"}))

    local_model._pull("qwen3-vl:2b-instruct")

    assert forgotten == [True]


def test_a_running_download_is_not_started_twice(monkeypatch):
    started = []
    monkeypatch.setattr(threading, "Thread", lambda target, args, daemon: started.append(args) or _NoThread())

    first = local_model.start_download("qwen3-vl:2b-instruct")
    second = local_model.start_download("qwen3-vl:2b-instruct")

    assert first["status"] == second["status"] == "running"
    assert started == [("qwen3-vl:2b-instruct",)]


class _NoThread:
    def start(self):
        pass


def test_on_gpu_reads_the_loaded_models(monkeypatch):
    answers = {"gpu": {"models": [{"name": "m", "size_vram": 2_000_000}]},
               "cpu": {"models": [{"name": "m", "size_vram": 0}]},
               "none": {"models": []}}
    for kind, expected in (("gpu", True), ("cpu", False), ("none", None)):
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda url, timeout, kind=kind: io.BytesIO(json.dumps(answers[kind]).encode()))
        assert local_model.on_gpu("http://ollama:11434") is expected


def test_the_private_model_comes_from_the_private_mode():
    assert local_model.private_model() == "qwen3-vl:2b-instruct"
