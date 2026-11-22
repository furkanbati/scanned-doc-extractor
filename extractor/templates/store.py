"""Rule sets on disk: one YAML file per rule set, named by the user.

They live in templates/ at the project root, or in TEMPLATES_DIR (the Docker
setup keeps them on a volume). They are the user's own data and never go into
git.
"""

import os
import re
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def directory() -> Path:
    return Path(os.getenv("TEMPLATES_DIR") or ROOT / "templates")


def slug(name: str) -> str:
    """A file name for the rule set's name: "ABC Tedarik Ş.A." -> "abc-tedarik-s-a"."""

    ascii_name = unicodedata.normalize("NFKD", name.replace("ı", "i").replace("İ", "I"))
    ascii_name = ascii_name.encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not text:
        raise ValueError("The name needs at least one letter or digit")

    return text[:60]


def path(name: str) -> Path:
    return directory() / f"{slug(name)}.yaml"


def save(template: dict, replace: bool = False) -> Path:
    target = path(template["name"])
    if target.exists() and not replace:
        raise FileExistsError(f"A rule set named {template['name']!r} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(template, allow_unicode=True, sort_keys=False), encoding="utf-8")

    return target


def load(name: str) -> dict:
    """A rule set by its name or file name (slug)."""

    target = path(name)
    if not target.is_file():
        raise KeyError(f"No rule set named {name!r}")

    return yaml.safe_load(target.read_text(encoding="utf-8"))


def names() -> dict[str, dict]:
    """Every saved rule set: file name -> name, date, examples, rule per field."""

    found = {}
    for file in sorted(directory().glob("*.yaml")):
        template = yaml.safe_load(file.read_text(encoding="utf-8"))
        found[file.stem] = {
            "name": template["name"],
            "doctype": template["doctype"],
            "created": template["created"],
            "examples": template["examples"],
            "fields": {name: rule["kind"] for name, rule in template["fields"].items()},
            # Learned from a teacher's answers (extractor/templates/auto.py),
            # and the documents left to check against the teacher.
            "auto": bool(template.get("auto")),
            "probation": template.get("probation", 0),
        }

    return found


def delete(name: str) -> None:
    target = path(name)
    if not target.is_file():
        raise KeyError(f"No rule set named {name!r}")
    target.unlink()


def all_templates() -> dict[str, dict]:
    """Every saved rule set in full: file name -> rule set."""

    return {file.stem: yaml.safe_load(file.read_text(encoding="utf-8"))
            for file in sorted(directory().glob("*.yaml"))}
