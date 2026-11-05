"""What the service offers: modes for users, engines for experts, providers still planned.

A mode (profiles.yaml) names the experiment config it runs. Whether an engine
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

from evaluation.run import CONFIG_DIR, load_config

ROOT = Path(__file__).parent
PROFILES_PATH = ROOT / "profiles.yaml"
RESULTS_DIR = ROOT / "evaluation" / "results"

CLOUD_EXTRACTORS = {"openai_vision"}
OLLAMA_EXTRACTORS = {"local_llm", "local_vision"}


def load_profiles(path: Path = PROFILES_PATH) -> dict:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def measured(method: str, results_dir: Path = RESULTS_DIR) -> dict | None:
    """The method's numbers on the test receipts, or None if it was not run."""

    path = results_dir / method / "report.json"
    if not path.exists():
        return None

    report = json.loads(path.read_text(encoding="utf-8"))
    usd = report.get("usd_per_receipt")

    return {
        "receipts": report["receipts"],
        "field_accuracy": report["field_accuracy"]["mean"],
        "seconds_per_document": report["seconds_per_receipt"],
        "usd_per_1000": usd * 1000 if usd is not None else 0.0,
    }


def ollama_reachable() -> bool:
    return _ollama_reachable(int(time.time() // 30))


@lru_cache(maxsize=4)
def _ollama_reachable(_period: int) -> bool:
    """Checked at most once every 30 seconds (the argument is the time slot)."""

    host = os.getenv("OLLAMA_HOST", "http://ollama:11434").rstrip("/")

    def ping() -> bool:
        try:
            with urllib.request.urlopen(f"{host}/api/tags", timeout=1.5):
                return True
        except OSError:
            return False

    # The urlopen timeout does not cover the name lookup, which can take
    # seconds when no "ollama" container exists; cap the whole check.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(ping).result(timeout=1.5)
    except TimeoutError:
        return False
    finally:
        pool.shutdown(wait=False)


def unavailable_reason(config: dict) -> str | None:
    """Why this engine cannot run here, or None when it can."""

    if config["fields"] in CLOUD_EXTRACTORS and not os.getenv("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set on the server"
    if config["fields"] in OLLAMA_EXTRACTORS and not ollama_reachable():
        return "The local model server (Ollama) is not running"

    return None


def engines() -> dict[str, dict]:
    result = {}

    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        config = load_config(path.stem)
        reason = unavailable_reason(config)
        result[path.stem] = {
            "description": config.get("description", ""),
            "doctype": config["doctype"],
            "ocr": config["ocr"],
            "fields": config["fields"],
            "model": config.get("fields_options", {}).get("model"),
            "leaves_machine": config["fields"] in CLOUD_EXTRACTORS,
            "available": reason is None,
            "unavailable_reason": reason,
            "measured": measured(path.stem),
        }

    return result


def profiles() -> dict:
    """The modes with their engine's availability and numbers, and the default."""

    spec = load_profiles()
    all_engines = engines()
    result = {}

    for name, profile in spec["profiles"].items():
        engine = all_engines[profile["method"]]
        result[name] = {
            "label": profile["label"],
            "description": profile["description"].strip(),
            "method": profile["method"],
            "leaves_machine": engine["leaves_machine"],
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
