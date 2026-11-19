"""What the service offers: modes for users, engines for experts, providers still planned.

A mode (profiles.yaml) names the engine it runs (extractor/engines). Whether an engine
can run here depends on this machine: the OpenAI engine needs a key, the
local-model engines need an Ollama server.
"""

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from functools import lru_cache
from pathlib import Path

import yaml

from extractor.pipeline import engine_names, load_engine, reads_line_items
from extractor.schema import load_doctype

ROOT = Path(__file__).parent
PROFILES_PATH = ROOT / "profiles.yaml"

CLOUD_EXTRACTORS = {"openai_vision"}
OLLAMA_EXTRACTORS = {"local_llm", "local_vision"}


def load_profiles(path: Path = PROFILES_PATH) -> dict:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def measured(config: dict) -> dict | None:
    """The engine's numbers on the test receipts, or None if it was not measured."""

    return config.get("measured")


def ollama_host() -> str:
    host = os.getenv("OLLAMA_HOST", "http://ollama:11434").rstrip("/")
    # On Windows "localhost" tries IPv6 first and waits about 2 s before
    # falling back to IPv4, longer than the whole check may take.
    return host.replace("://localhost:", "://127.0.0.1:")


def ollama_models() -> set[str] | None:
    """The models the Ollama server has, or None when it cannot be reached."""

    return _ollama_models(int(time.time() // 30))


@lru_cache(maxsize=4)
def _ollama_models(_period: int) -> set[str] | None:
    """Checked at most once every 30 seconds (the argument is the time slot)."""

    url = f"{ollama_host()}/api/tags"

    def ask() -> set[str] | None:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                return {model["name"] for model in json.load(response).get("models", [])}
        except (OSError, ValueError):
            return None

    # The urlopen timeout does not cover the name lookup, which can take
    # seconds when no "ollama" container exists; cap the whole check.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(ask).result(timeout=1.5)
    except TimeoutError:
        return None
    finally:
        pool.shutdown(wait=False)


def forget_ollama() -> None:
    """Check the server again on the next call, e.g. after a model was downloaded."""

    _ollama_models.cache_clear()


def has_model(models: set[str], model: str) -> bool:
    return model in models or f"{model}:latest" in models


def unavailable_reason(config: dict) -> str | None:
    """Why this engine cannot run here, or None when it can."""

    if config["fields"] in CLOUD_EXTRACTORS and not os.getenv("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set on the server"
    if config["fields"] in OLLAMA_EXTRACTORS:
        models = ollama_models()
        if models is None:
            return "The local model server (Ollama) is not running"
        model = config.get("fields_options", {}).get("model")
        if model and not has_model(models, model):
            return f"The local model {model} is not downloaded yet"

    return None


def engines() -> dict[str, dict]:
    result = {}

    for name in engine_names():
        config = load_engine(name)
        reason = unavailable_reason(config)
        result[name] = {
            "description": config.get("description", ""),
            "doctype": config["doctype"],
            "ocr": config["ocr"],
            "fields": config["fields"],
            "model": config.get("fields_options", {}).get("model"),
            "leaves_machine": config["fields"] in CLOUD_EXTRACTORS,
            # Returns purchased items with their amounts next to the fields.
            "line_items": reads_line_items(config, load_doctype(config["doctype"])),
            "available": reason is None,
            "unavailable_reason": reason,
            "measured": measured(config),
        }

    return result


def profiles() -> dict:
    """The modes with their engine's availability and numbers, and the default."""

    spec = load_profiles()
    all_engines = engines()
    result = {}

    for name, profile in spec["profiles"].items():
        method = profile["method"]
        engine = all_engines[method]
        fallback_reason = None
        fallback = profile.get("fallback")
        if not engine["available"] and fallback and all_engines[fallback]["available"]:
            fallback_reason = engine["unavailable_reason"]
            method, engine = fallback, all_engines[fallback]
        result[name] = {
            "label": profile["label"],
            "description": profile["description"].strip(),
            # The engine this mode runs now; differs from profiles.yaml's
            # `method` while that engine cannot run here.
            "method": method,
            "fallback_reason": fallback_reason,
            "leaves_machine": engine["leaves_machine"],
            "line_items": engine["line_items"],
            "available": engine["available"],
            "unavailable_reason": engine["unavailable_reason"],
            "measured": engine["measured"],
        }

    return {
        "default": default_profile(result, spec["order"]),
        "profiles": result,
        "planned": spec.get("planned", []),
    }


def default_profile(profiles: dict, order: list[str]) -> str:
    wanted = os.getenv("DEFAULT_PROFILE")
    if wanted in profiles:
        return wanted

    for name in order:
        if profiles[name]["available"]:
            return name

    return order[0]
