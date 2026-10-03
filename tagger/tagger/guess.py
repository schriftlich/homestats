"""Vorschläge für saubere Tags aus YouTube-Titeln, Kanalnamen und Ordnernamen.

Reine Funktionen ohne Dateizugriff – damit gut testbar.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

MUSIK = "musik"
HOERBUCH = "hoerbuch"

# yt-dlp ersetzt in Dateinamen verbotene Zeichen durch Vollbreite-Varianten
FULLWIDTH = str.maketrans({
    "＂": '"', "：": ":", "｜": "|", "⧸": "/", "⧹": "\\", "＊": "*", "？": "?", "＜": "<", "＞": ">",
})

# Klammerzusätze, die nur etwas über das Video sagen, nicht über das Lied
NOISE_BRACKET = re.compile(
    r"\s*[\(\[]\s*(?:official\s+)?(?:music\s+|lyrics?\s+)?"
    r"(?:audio(?:\s+only)?|video|visuali[sz]er|lyrics?(?:\s+video)?|lyrics\s+and\s+chords|hd|hq|4k|full\s+album)"
    r"\s*[\)\]]",
    re.I,
)
NOISE_TAIL = re.compile(r"\s+(?:with\s+lyrics|\(?official\)?)\s*$", re.I)

ALBUM_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official\s+)?(?:album\s+playlist|full\s+album|playlist|lyric\s+videos|official\s+lyric\s+videos)\s*[\)\]]",
    re.I,
)

SEP = re.compile(r"\s+[-–—~]\s+|(?<=\S)[-–—]\s+|\s*:\s+|\s+\|\s+")

AUDIOBOOK_WORDS = re.compile(r"hörbuch|hörspiel|hoerbuch|hoerspiel|audiobook|gelesen von|\bliest\b|lesung|\broman\b|kapitel", re.I)
HOERSPIEL_WORDS = re.compile(r"hörspiel|hoerspiel", re.I)
# Wörter, die in Hörbuch-Titeln nur Beiwerk sind
BOOK_NOISE = re.compile(
    r"\b(?:komplettes|komplett|kompletter|ganzes|vollständiges|ungekürzt|ungekürztes|deutsch|german|"
    r"spannendes|spannender|hörbuch(?:-roman)?|hörspiel|audiobook|klassiker(?:-roman)?|jugendroman|roman|teil\s+\d+)\b",
    re.I,
)
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to",
               "der", "die", "das", "und", "von", "im", "in", "am", "zu", "des"}
KEEP_UPPER = {"EP", "DJ", "NPR", "USA", "UK", "BBC", "TV", "CD", "DVD", "EO"}
CHANNEL_NOISE = re.compile(r"^@|\s*-\s*topic$|vevo$|\s+official$|^official\s+", re.I)


@dataclass
class Track:
    """Eingabe: was die Datei heute hat."""
    id: str
    relpath: str          # relativ zum Eingangsordner, z. B. "FOLK! (Full Album)/Rend Collective - Burn.mp3"
    title: str = ""
    artist: str = ""      # bei YouTube: Kanalname
    genre: str = ""
    date: str = ""
    duration: float = 0.0

    @property
    def folder(self) -> str:
        return self.relpath.rsplit("/", 1)[0] if "/" in self.relpath else ""

    @property
    def stem(self) -> str:
        name = self.relpath.rsplit("/", 1)[-1]
        return name.rsplit(".", 1)[0]


@dataclass
class Proposal:
    """Ausgabe: vorgeschlagene Tags pro Datei."""
    title: str = ""
    artist: str = ""
    track: int | None = None
    sure: bool = False     # Interpret sicher erkannt (nicht nur Kanalname)
    notes: list[str] = field(default_factory=list)


@dataclass
class Group:
    """Mehrere Dateien, die zusammengehören (Ordner) – oder eine einzelne Datei im Eingang."""
    id: str
    folder: str
    kind: str
    album: str = ""
    albumartist: str = ""     # bei Hörbüchern: Autor
    year: str = ""
    genre: str = ""
    narrator: str = ""
    tracks: list[Track] = field(default_factory=list)
    proposals: dict[str, Proposal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def sure(self) -> bool:
        if self.kind == HOERBUCH:
            return bool(self.albumartist and self.album)
        return bool(self.album and self.albumartist) and all(p.sure for p in self.proposals.values())


# --- Text-Helfer --------------------------------------------------------------

def norm(s: str) -> str:
    """Zum Vergleichen: Kleinbuchstaben, ohne Akzente, 'the', Apostrophe, Satzzeichen."""
    s = unicodedata.normalize("NFKD", s.translate(FULLWIDTH))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"^the\s+", "", s.strip())
    return re.sub(r"[^a-z0-9]", "", s)


def tidy(s: str) -> str:
    s = s.translate(FULLWIDTH)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" -–—~|:")


def strip_quotes(s: str) -> str:
    s = s.strip()
    for a, b in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("'", '"'), ("„", "“")):
        if len(s) > 2 and s.startswith(a) and s.endswith(b):
            return s[1:-1].strip()
    return s


def fix_caps(s: str) -> str:
    """GROSSGESCHRIEBENE Titel in normale Schreibweise bringen; gemischte bleiben unverändert."""
    words = re.findall(r"[^\W\d_]+", s)
    long_words = [w for w in words if len(w) > 1]
    if not long_words or sum(w.isupper() for w in long_words) / len(long_words) < 0.6:
        return s

    def fix(m: re.Match) -> str:
        w = m.group(0)
        if not w.isupper() or len(w) == 1 or re.fullmatch(r"[IVX]+", w) or w in KEEP_UPPER:
            return w
        low = w.lower()
        if low in SMALL_WORDS and m.start() > 0 and s[m.start() - 1] not in "([":
            return low
        return w.capitalize()

    out = re.sub(r"[^\W\d_]+(?:'[^\W\d_]+)?", fix, s)
    return out[:1].upper() + out[1:]


def clean_title(s: str) -> str:
    s = tidy(s)
    prev = None
    while prev != s:
        prev = s
        s = NOISE_BRACKET.sub("", s)
        s = NOISE_TAIL.sub("", s)
        s = tidy(s)
    return s


def clean_channel(s: str) -> str:
    s = tidy(s)
    if s.startswith("@") and " " not in s:   # Handle wie "@TheOhHellosMusic"
        s = re.sub(r"(?<=[a-zäöü])(?=[A-ZÄÖÜ])", " ", s[1:])
        s = re.sub(r"\s+(?:Music|Musik|Official|TV|VEVO)$", "", s)
    prev = None
    while prev != s:
        prev = s
        s = CHANNEL_NOISE.sub("", s).strip()
    return s


def year_of(date: str) -> str:
    m = re.match(r"(\d{4})", date or "")
    return m.group(1) if m else ""


def clean_album(folder: str, artist: str = "") -> str:
    name = folder.rsplit("/", 1)[-1].translate(FULLWIDTH)
    name = re.sub(r"(?<=\S)_ ", ": ", name)       # yt-dlp: "CAMPFIRE II_ SIMPLICITY"
    name = name.replace(" _ ", " / ")
    name = ALBUM_NOISE.sub("", name)
    name = tidy(name)
    if artist:
        parts = SEP.split(name, maxsplit=1)
        if len(parts) == 2 and norm(parts[0]) == norm(artist):
            name = parts[1]
    return fix_caps(tidy(name))


# --- Art erkennen -------------------------------------------------------------

def detect_kind(t: Track) -> str:
    text = f"{t.title} {t.stem}"
    minutes = (t.duration or 0) / 60
    if minutes >= 45 and (t.genre or "").lower() != "music":
        return HOERBUCH
    if minutes >= 20 and AUDIOBOOK_WORDS.search(text):
        return HOERBUCH
    if minutes >= 90:
        return HOERBUCH
    return MUSIK


# --- Musik --------------------------------------------------------------------

COLLAB = re.compile(r"\s*(?:,|&|\bx\b|\bfeat\.?|\bft\.?|\bvs\.?)\s*", re.I)


def _matches(part: str, artist: str) -> bool:
    """Teil ist der Interpret – oder eine Zusammenarbeit, an der er beteiligt ist."""
    a = norm(artist)
    if not a:
        return False
    return any(norm(name) == a for name in [part, *COLLAB.split(part)] if name)


def _contains(text: str, artist: str) -> bool:
    a = norm(artist)
    return len(a) >= 4 and a in norm(text)


def _quoted_title(rest: str) -> str:
    """'"Trees" ~ Newport Folk Fest 2014' -> 'Trees (Newport Folk Fest 2014)'"""
    m = re.match(r'^["“„\'‘](?P<q>.+?)["”“\'’]\s*[-–—~:]?\s*(?P<rest>.*)$', rest)
    if not m:
        return rest
    q, more = m.group("q").strip(), tidy(m.group("rest"))
    if not more:
        return q
    if more.startswith("(") or more.startswith("["):
        return f"{q} {more}"
    return f"{q} ({more})"


def split_music(raw: str, channel: str, known: list[str] | None = None, fallback: str = "") -> Proposal:
    """'Rend Collective - Burn (Official Audio)' -> Titel 'Burn', Interpret 'Rend Collective'.

    known: bekannte Interpreten (Albuminterpret, andere Kanäle im Eingang, gemerkte Zuordnungen),
    werden vor dem Kanalnamen geprüft. fallback: Interpret, wenn nichts passt (sonst der Kanal).
    """
    title = clean_title(raw)
    channel = clean_channel(channel)
    fallback = fallback or channel
    candidates = []
    for c in [*(known or []), channel]:
        if c and norm(c) not in [norm(x) for x in candidates]:
            candidates.append(c)

    def done(t: str, artist: str, sure: bool = True, track: int | None = None) -> Proposal:
        t = _quoted_title(strip_quotes(tidy(t)))
        return Proposal(title=fix_caps(t), artist=artist, track=track, sure=sure)

    # "Mountainkind Hymnal #3 - Titel"
    m = re.match(r"^(?P<series>.+?)\s*#(?P<n>\d{1,3})\s*[-–—:]\s*(?P<title>.+)$", title)
    if m:
        artist = next((c for c in candidates if _contains(m.group("series"), c)), "")
        return done(m.group("title"), artist or fallback, sure=bool(artist), track=int(m.group("n")))

    # "<Titel>" from Rend Collective
    m = re.match(r'^(?P<title>["\'“‘].+?["\'”’])\s+from\s+(?P<artist>.+)$', title, re.I)
    if m:
        artist = next((c for c in candidates if _matches(m.group("artist"), c)), tidy(m.group("artist")))
        return done(m.group("title"), _artist_name(artist, artist))

    parts = [p for p in SEP.split(title) if p.strip()]
    if len(parts) >= 2:
        for cand in candidates:
            if _matches(parts[0], cand):
                return done(" - ".join(parts[1:]), _artist_name(tidy(parts[0]), cand))
            if _matches(parts[-1], cand):
                return done(" - ".join(parts[:-1]), _artist_name(tidy(parts[-1]), cand))

    # Titel beginnt mit dem Interpreten, aber ohne Trennzeichen: "The Oh Hellos FCA Mvmt I ..."
    words = title.split()
    for cand in candidates:
        for i in range(1, min(len(words), 6)):
            if norm(" ".join(words[:i])) == norm(cand):
                rest = " ".join(words[i:])
                rest = re.sub(r"^(?:perform|performs|performing|live)\s+", "", rest, flags=re.I)
                return done(rest, cand)

    # Interpret kommt irgendwo im Titel vor: Titel bleibt, Interpret ist (ziemlich) klar
    for cand in candidates:
        if _contains(title, cand):
            return done(title, cand, sure=False)

    return done(title, fallback, sure=False)


def _artist_name(found: str, cand: str) -> str:
    """Schreibweise des Kanals bevorzugen ('The Oh Hello's' -> 'The Oh Hellos'), Zusätze behalten."""
    if norm(found) == norm(cand):
        return cand
    if found.isupper():
        return fix_caps(found)
    return found


# --- Hörbücher ----------------------------------------------------------------

NAME = r"[A-ZÄÖÜ][\wäöüß.\-]+(?:\s+(?:von\s+|van\s+|de\s+)?[A-ZÄÖÜ][\wäöüß.\-]+){1,3}"


def _looks_like_name(s: str) -> bool:
    return bool(re.fullmatch(NAME, s.strip())) and not BOOK_NOISE.search(s)


def split_book(raw: str) -> dict:
    """'Oliver Twist – Charles Dickens, Teil 1 | Hörbuch | Gelesen von Sven Görtz'"""
    s = tidy(raw)
    s = re.sub(r"^[^\w\"'„“(]+", "", s)  # Emojis am Anfang
    out = {"book": "", "author": "", "narrator": "", "part": None, "year": "",
           "hoerspiel": bool(HOERSPIEL_WORDS.search(s))}

    segments = [tidy(x) for x in s.split("|")]
    main, extra = segments[0], segments[1:]
    for seg in extra:
        m = re.match(rf"^(?:gelesen\s+von|sprecher(?:in)?:?|read\s+by)\s+({NAME})", seg, re.I)
        if m:
            out["narrator"] = m.group(1)
            continue
        m = re.match(rf"^({NAME})\s+liest$", seg)
        if m:
            out["narrator"] = m.group(1)

    # "Hörbuch komplett: Titel - Autor"
    main = re.sub(r"^(?:hörbuch|hörspiel|audiobook)[^:]*:\s*", "", main, flags=re.I)
    m = re.search(r"\((\d{4})\)", main)
    if m:
        out["year"] = m.group(1)
        main = tidy(main.replace(m.group(0), ""))
    m = re.search(r",?\s*\b(?:Teil|Folge|Part|CD)\s+(\d{1,3})\b", main, re.I)
    if m:
        out["part"] = int(m.group(1))
        main = tidy(main[: m.start()] + main[m.end():])
    m = re.search(rf"\bnach\s+({NAME})", main)
    if m:
        out["author"] = m.group(1)
        main = tidy(main[: m.start()])

    parts = [tidy(p) for p in re.split(r"\s+[-–—]\s+", main) if tidy(p)]
    book_parts = []
    for p in parts:
        p_clean = tidy(re.sub(r"[\(\[]\s*[\)\]]", "", BOOK_NOISE.sub("", p)))
        if not out["author"] and _looks_like_name(p) and book_parts:
            out["author"] = p
        elif p_clean:
            book_parts.append(p_clean)
    book = " - ".join(book_parts)
    book = tidy(re.sub(r"[\(\[]\s*[\)\]]", "", BOOK_NOISE.sub("", book)))
    out["book"] = fix_caps(book)
    return out


# --- Gruppen ------------------------------------------------------------------

def most_common(values, default: str = "") -> str:
    values = [v for v in values if v]
    if not values:
        return default
    counts = Counter(values)
    best = max(counts.values())
    return sorted(v for v, c in counts.items() if c == best)[0]


@dataclass
class Memory:
    """Was der Nutzer korrigiert hat – fließt in künftige Vorschläge ein."""
    artists: dict[str, str] = field(default_factory=dict)   # norm(Kanal) -> Interpret
    authors: dict[str, str] = field(default_factory=dict)   # norm(Buchtitel) -> Autor
    kinds: dict[str, str] = field(default_factory=dict)     # Gruppen-ID -> Art


def channel_artist(channel: str, mem: Memory) -> str:
    c = clean_channel(channel)
    return mem.artists.get(norm(c), "") if c else ""


def build_groups(tracks: list[Track], mem: Memory | None = None) -> list[Group]:
    """Dateien in Ordnern bilden eine Gruppe (Album), Dateien direkt im Eingang je eine eigene."""
    mem = mem or Memory()
    buckets: dict[str, list[Track]] = {}
    for t in sorted(tracks, key=lambda t: t.relpath.lower()):
        key = t.folder if t.folder else t.relpath
        buckets.setdefault(key, []).append(t)

    # Kanäle, die im ganzen Eingang als Musik-Interpreten auftauchen (z. B. "The Oh Hellos")
    music_channels = Counter(clean_channel(t.artist) for t in tracks
                             if t.artist and detect_kind(t) == MUSIK)
    known_global = [mem.artists.get(norm(c), c) for c, n in music_channels.most_common() if n >= 2]

    groups = []
    for key, items in buckets.items():
        gid = "g" + _short_hash(key)
        kinds = [detect_kind(t) for t in items]
        kind = mem.kinds.get(gid) or most_common(kinds, MUSIK)
        g = Group(id=gid, folder=items[0].folder, kind=kind, tracks=items)
        if kind == HOERBUCH:
            _fill_book(g, mem)
        else:
            _fill_music(g, mem, known_global)
        groups.append(g)
    return groups


def _fill_music(g: Group, mem: Memory, known_global: list[str]) -> None:
    remembered = [a for a in (channel_artist(t.artist, mem) for t in g.tracks) if a]
    first = {t.id: split_music(t.title or t.stem, t.artist, remembered + known_global) for t in g.tracks}
    album_artist = most_common([p.artist for p in first.values() if p.sure])
    if not album_artist:
        album_artist = most_common(remembered) or _short_channel(g, most_common([p.artist for p in first.values()]))
    for t in g.tracks:
        p = split_music(t.title or t.stem, t.artist, [album_artist] + remembered + known_global,
                        fallback=channel_artist(t.artist, mem) or album_artist)
        if not p.sure and norm(p.artist) in {norm(a) for a in remembered}:
            p.sure = True   # vom Nutzer schon einmal so zugeordnet
        g.proposals[t.id] = p
    # Kanal = Albuminterpret und der Großteil des Ordners ist sicher erkannt -> auch sicher
    majority = sum(p.sure for p in g.proposals.values()) * 2 >= len(g.tracks)
    for t in g.tracks:
        p = g.proposals[t.id]
        ch = clean_channel(t.artist)
        if not p.sure and majority and norm(p.artist) == norm(album_artist) and \
                norm(album_artist) in (norm(ch), norm(_short_channel(g, ch))):
            p.sure = True
        if not p.sure:
            p.notes.append("Interpret unsicher")
    g.albumartist = album_artist
    g.year = most_common([year_of(t.date) for t in g.tracks])
    if g.folder:
        g.album = clean_album(g.folder, album_artist)
    else:
        g.album = g.proposals[g.tracks[0].id].title   # Einzeltitel: Album = Titel (Single)
    if not g.albumartist:
        g.warnings.append("Interpret fehlt")


def _short_channel(g: Group, channel: str) -> str:
    """'Mountainkind Music' -> 'Mountainkind', wenn der kurze Name in den Titeln/im Ordner vorkommt."""
    m = re.match(r"^(.+?)\s+(?:music|musik|official|band|records)$", channel or "", re.I)
    if m:
        short = m.group(1)
        texts = [g.folder] + [t.title for t in g.tracks]
        if any(_contains(x, short) for x in texts):
            return short
    return channel


def _fill_book(g: Group, mem: Memory) -> None:
    infos = {t.id: split_book(t.title or t.stem) for t in g.tracks}
    first = infos[g.tracks[0].id]
    g.album = most_common([i["book"] for i in infos.values()]) or (clean_album(g.folder) if g.folder else "")
    g.albumartist = most_common([i["author"] for i in infos.values()]) or mem.authors.get(norm(g.album), "")
    g.narrator = most_common([i["narrator"] for i in infos.values()])
    g.year = first["year"] or ""
    g.genre = "Hörspiel" if any(i["hoerspiel"] for i in infos.values()) else "Hörbuch"
    multi = len(g.tracks) > 1
    for n, t in enumerate(g.tracks, 1):
        i = infos[t.id]
        part = i["part"] or (n if multi else None)
        title = g.album + (f" - Teil {part}" if part and (multi or i["part"]) else "")
        g.proposals[t.id] = Proposal(title=title, artist=g.albumartist, track=part, sure=bool(g.albumartist))
    if not g.albumartist:
        g.warnings.append("Autor nicht erkannt – bitte eintragen")


def _short_hash(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode()).hexdigest()[:10]


# --- Dateinamen ---------------------------------------------------------------

def safe_name(s: str, limit: int = 120) -> str:
    s = s.translate(str.maketrans({"/": "-", "\\": "-", ":": " -", "*": "", "?": "", '"': "'", "<": "", ">": "", "|": "-"}))
    s = re.sub(r"[\x00-\x1f]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > limit:
        s = s[:limit].rstrip(" .")
    return s or "Unbekannt"


def book_folder(album: str, narrator: str, genre: str) -> str:
    name = album or "Unbekannt"
    if genre == "Hörspiel":
        name += " (Hörspiel)"
    elif narrator:
        name += " {" + narrator + "}"   # Audiobookshelf liest den Sprecher aus {…}
    return safe_name(name)


def target_relpath(kind: str, album: str, albumartist: str, title: str, track: int | None,
                   narrator: str = "", genre: str = "", multi: bool = True) -> str:
    """Pfad relativ zum Download-Ordner von Umbrel."""
    if kind == HOERBUCH:
        return "/".join([
            "audiobooks", safe_name(albumartist or "Unbekannt"),
            book_folder(album, narrator, genre), safe_name(title or album) + ".mp3",
        ])
    prefix = f"{track:02d} - " if track and multi else ""
    return "/".join([
        "music", safe_name(albumartist or "Unbekannt"), safe_name(album or "Singles"),
        prefix + safe_name(title) + ".mp3",
    ])
