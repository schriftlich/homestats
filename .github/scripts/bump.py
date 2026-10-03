"""Setzt Version, Image (Tag + Digest) und Release Notes im Umbrel-Paket.

Aufruf: python bump.py <version> <image-ref> [<notes-datei>]
Paket und Image lassen sich über BUMP_APP und BUMP_IMAGE wählen
(Standard: Zählerstände-App).
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / os.environ.get("BUMP_APP", "homestats-zaehler")
IMAGE = os.environ.get("BUMP_IMAGE", "ghcr.io/schriftlich/homestats-zaehler")


def release_notes_block(notes: str) -> str:
    lines = [ln.rstrip() for ln in notes.strip().splitlines() if ln.strip()]
    if not lines:
        return 'releaseNotes: ""\n\n'
    # Jede Zeile als eigener Absatz, damit Aufzählungen erhalten bleiben
    body = "\n\n\n".join(f"  {ln}" for ln in lines)
    return f"releaseNotes: >-\n{body}\n\n"


def main() -> None:
    version, image_ref = sys.argv[1], sys.argv[2]
    notes = Path(sys.argv[3]).read_text() if len(sys.argv) > 3 else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"Ungültige Version: {version}")
    if not re.fullmatch(re.escape(IMAGE) + r":[\w.-]+@sha256:[0-9a-f]{64}", image_ref):
        sys.exit(f"Ungültige Image-Referenz: {image_ref}")

    manifest = APP / "umbrel-app.yml"
    text = manifest.read_text()
    text, n = re.subn(r'(?m)^version: .*$', f'version: "{version}"', text)
    assert n == 1, "version nicht gefunden"
    # releaseNotes-Block bis zum nächsten Schlüssel auf oberster Ebene ersetzen
    text, n = re.subn(r"(?ms)^releaseNotes:.*?(?=^\S|\Z)", release_notes_block(notes), text)
    assert n == 1, "releaseNotes nicht gefunden"
    manifest.write_text(text)

    compose = APP / "docker-compose.yml"
    text, n = re.subn(rf"(?m)^(\s*image: ){re.escape(IMAGE)}[:@]\S*$", rf"\g<1>{image_ref}", compose.read_text())
    assert n == 1, "image nicht gefunden"
    compose.write_text(text)


if __name__ == "__main__":
    main()
