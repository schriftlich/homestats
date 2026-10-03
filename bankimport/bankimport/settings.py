"""Einstellungen (Firefly-Adresse und Zugangsschlüssel) – gespeichert im Datenordner."""
import json
import os
from pathlib import Path

DEFAULT_URL = "http://firefly-iii_server_1:8080"


def _file() -> Path:
    return Path(os.environ.get("BANKIMPORT_DATA_DIR", "/data")) / "settings.json"


def load() -> dict:
    data = {"firefly_url": os.environ.get("FIREFLY_URL", DEFAULT_URL),
            "firefly_token": os.environ.get("FIREFLY_TOKEN", ""),
            "firefly_link": os.environ.get("FIREFLY_LINK", "")}
    f = _file()
    if f.exists():
        try:
            stored = json.loads(f.read_text())
            data.update({k: v for k, v in stored.items() if v})
        except (ValueError, OSError):
            pass
    return data


def save(**values) -> None:
    f = _file()
    current = {}
    if f.exists():
        try:
            current = json.loads(f.read_text())
        except ValueError:
            current = {}
    current.update(values)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(f)
