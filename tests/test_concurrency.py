"""A document being read does not hold up other requests."""

import threading

from fastapi.testclient import TestClient

import api
from tests.test_doctypes_api import png


def test_health_answers_while_a_document_is_read(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(10)
        raise RuntimeError("done waiting")

    monkeypatch.setattr(api, "run_engine", slow)
    monkeypatch.setattr(api, "choose_method", lambda profile, method, template=None: (None, ["e5"]))
    answered = []
    # One event loop for both requests, as in the server.
    with TestClient(api.app) as client:
        reading = threading.Thread(target=lambda: client.post("/extract", files={"file": ("f.png", png(41))}))
        reading.start()
        try:
            assert started.wait(10)
            asking = threading.Thread(target=lambda: answered.append(client.get("/health").json()))
            asking.start()
            asking.join(3)
            # Answered while the extraction above is still blocked.
            assert answered == [{"status": "ok"}]
        finally:
            release.set()
            reading.join(10)
