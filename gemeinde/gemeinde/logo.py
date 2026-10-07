"""Logo der Gemeinde für die PDF-Kopfzeile. Liegt als PNG im Datenordner."""
from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from .db import db_path

MAX_BYTES = 10 * 1024 * 1024
MAX_WIDTH = 1600


class LogoError(ValueError):
    pass


def path():
    return db_path().parent / "logo.png"


def load() -> bytes | None:
    p = path()
    return p.read_bytes() if p.exists() else None


def normalize(data: bytes) -> bytes:
    """Prüft das Bild, verkleinert es bei Bedarf und speichert es als PNG."""
    if not data:
        raise LogoError("Keine Datei ausgewählt.")
    if len(data) > MAX_BYTES:
        raise LogoError("Das Bild ist größer als 10 MB.")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise LogoError("Die Datei ist kein lesbares Bild (PNG oder JPG).")
    im = im.convert("RGBA")
    bbox = im.getbbox()  # transparente Ränder abschneiden
    if bbox:
        im = im.crop(bbox)
    if im.width > MAX_WIDTH:
        im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def save(data: bytes) -> None:
    png = normalize(data)
    tmp = path().with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(path())


def delete() -> None:
    path().unlink(missing_ok=True)
