import hashlib
import json
from pathlib import Path

import pytest

from tagger.guess import (
    HOERBUCH, MUSIK, Memory, Track, build_groups, clean_album, detect_kind, fix_caps, split_book,
    split_music, target_relpath,
)

# Echte Titel/Tags aus dem MeTube-Ordner (Stand Okt. 2026)
CORPUS = json.loads((Path(__file__).parent / "corpus.json").read_text())


def tracks():
    return [Track(id=hashlib.sha1(r["path"].encode()).hexdigest()[:10], relpath=r["path"][2:],
                  title=r["title"] or "", artist=r["artist"] or "", genre=r["genre"] or "",
                  date=r["date"] or "", duration=r["dur"]) for r in CORPUS]


def group(groups, folder):
    return next(g for g in groups if (g.folder or g.tracks[0].relpath).startswith(folder))


def titles(g):
    return {g.proposals[t.id].title for t in g.tracks}


@pytest.mark.parametrize("raw, channel, title, artist", [
    ("Rend Collective - Burn (Official Audio)", "Rend Collective", "Burn", "Rend Collective"),
    ('"My Lighthouse" - Rend Collective (Official Audio)', "Rend Collective", "My Lighthouse", "Rend Collective"),
    ('"Joy" from Rend Collective (OFFICIAL LYRIC VIDEO)', "Rend Collective", "Joy", "Rend Collective"),
    ("Cold Is The Night - The Oh Hello's", "@TheOhHellosMusic", "Cold Is The Night", "The Oh Hellos"),
    ("Rend Collective - BEHOLD HE COMES (Audio)", "Rend Collective", "Behold He Comes", "Rend Collective"),
    ("Rend Collective- MY ADVOCATE (Audio)", "Rend Collective", "My Advocate", "Rend Collective"),
    ("The Campfire Story continues - REND COLLECTIVE", "Rend Collective", "The Campfire Story continues", "Rend Collective"),
    ("Rend Collective, Chris Renzema - Room At The Inn (Official Lyric Video)", "Rend Collective",
     "Room At The Inn", "Rend Collective, Chris Renzema"),
    ("Psalm 56 - Sons of Korah", "Sons of Korah", "Psalm 56", "Sons of Korah"),
    ("Rend Collective - Rescuer (Good News) [Official Music Video]", "Rend Collective", "Rescuer (Good News)", "Rend Collective"),
    ("Rend Collective - Nailed to the Cross (Live from Vancouver) with lyrics", "Rend Collective",
     "Nailed to the Cross (Live from Vancouver)", "Rend Collective"),
])
def test_split_music(raw, channel, title, artist):
    p = split_music(raw, channel)
    assert (p.title, p.artist, p.sure) == (title, artist, True)


def test_channel_is_not_taken_from_title_suffix():
    # "Audiotree" ist der Kanal, nicht der Interpret
    p = split_music("The Oh Hellos - Dear Wormwood - Audiotree Live", "Audiotree", ["The Oh Hellos"])
    assert p.artist == "The Oh Hellos" and p.title == "Dear Wormwood - Audiotree Live"
    p = split_music("The Oh Hellos: NPR Music Tiny Desk Concert", "NPR Music", ["The Oh Hellos"])
    assert p.artist == "The Oh Hellos"
    p = split_music("Songs at the Shop: Episode 4 with The Oh Hellos", "walrusaudioeffects", ["The Oh Hellos"])
    assert p.artist == "The Oh Hellos" and not p.sure


def test_quoted_live_titles():
    p = split_music('THE OH HELLO\'S "Trees" ~ Newport Folk Fest 2014', "Newport Folk Festival", ["The Oh Hellos"])
    assert (p.title, p.artist) == ("Trees (Newport Folk Fest 2014)", "The Oh Hellos")
    p = split_music("The Oh Hellos — 'This Will End' (Live)", "GBH Music", ["The Oh Hellos"])
    assert p.title == "This Will End (Live)"


def test_caps_and_album_names():
    assert fix_caps("I CHOOSE TO WORSHIP (WORSHIP CLUB VERSION)") == "I Choose to Worship (Worship Club Version)"
    assert fix_caps("Christmas in Belfast (Sláinte!)") == "Christmas in Belfast (Sláinte!)"
    assert clean_album("Rend Collective - CAMPFIRE II_ SIMPLICITY (Official Album Playlist)", "Rend Collective") == "Campfire II: Simplicity"
    assert clean_album("FOLK! (Full Album)") == "Folk!"
    assert clean_album("Rend Collective- The Art of Celebration (Official Lyric Videos)", "Rend Collective") == "The Art of Celebration"


def test_kind_detection():
    t = tracks()
    kinds = {x.relpath: detect_kind(x) for x in t}
    books = sorted(p for p, k in kinds.items() if k == HOERBUCH)
    assert len(books) == 8 and all("/" not in p for p in books)
    # Lange Musik-Mitschnitte bleiben Musik
    assert kinds["Socially Distant Worship Club/Rend Collective- Socially Distant Worship Club (Session One).mp3"] == MUSIK
    assert kinds["Live _ Sessions/The Oh Hellos： NPR Music Tiny Desk Concert.mp3"] == MUSIK


def test_split_book():
    b = split_book("🎧 Oliver Twist – Charles Dickens, Teil 1 ｜ Klassiker-Roman ｜ Hörbuch ｜ Gelesen von Sven Görtz")
    assert (b["book"], b["author"], b["narrator"], b["part"]) == ("Oliver Twist", "Charles Dickens", "Sven Görtz", 1)
    b = split_book("🎩 Tom Sawyers Abenteuer und Streiche – Mark Twain ｜ Jugendroman ｜ Hörbuch ｜ Jürgen Fritsche liest")
    assert (b["book"], b["author"], b["narrator"]) == ("Tom Sawyers Abenteuer und Streiche", "Mark Twain", "Jürgen Fritsche")
    b = split_book("OLIVER TWIST - Spannendes Hörspiel nach Charles Dickens (1957)")
    assert (b["book"], b["author"], b["year"], b["hoerspiel"]) == ("Oliver Twist", "Charles Dickens", "1957", True)
    b = split_book("Hörbuch komplett: Oliver Twist - Charles Dickens")
    assert (b["book"], b["author"]) == ("Oliver Twist", "Charles Dickens")
    assert split_book("Herr der Ringe Hörspiel komplett deutsch")["book"] == "Herr der Ringe"
    assert split_book("Die Abenteuer des Tom Sawyer (Komplettes Hörspiel)")["book"] == "Die Abenteuer des Tom Sawyer"
    assert split_book("Onkel Toms Hütte - ( Hörbuch-Roman  )")["book"] == "Onkel Toms Hütte"


def test_corpus_groups():
    gs = build_groups(tracks())
    assert len(gs) == 30
    sure = sum(p.sure for g in gs for p in g.proposals.values())
    assert sure >= 228, sure

    g = group(gs, "Mountainkind Hymnal")
    assert (g.albumartist, g.album) == ("Mountainkind", "Mountainkind Hymnal")
    assert sorted(p.track for p in g.proposals.values() if p.track) == list(range(1, 13))

    g = group(gs, "The Oh Hellos EP")
    assert g.albumartist == "The Oh Hellos" and titles(g) == {"Cold Is The Night", "Hello My Old Heart", "Lay Me Down", "Trees"}

    g = group(gs, "Rend Collective - CHOOSE TO WORSHIP")
    assert g.album == "Choose to Worship" and g.year == "2020" and g.sure

    g = group(gs, "Live _ Sessions")
    assert g.albumartist == "The Oh Hellos" and not g.sure

    g = group(gs, "🎧 Oliver Twist")
    assert g.kind == HOERBUCH and (g.albumartist, g.album, g.narrator) == ("Charles Dickens", "Oliver Twist", "Sven Görtz")
    g = group(gs, "Herr der Ringe")
    assert g.kind == HOERBUCH and not g.albumartist and g.warnings


def test_memory_is_used():
    mem = Memory(authors={"herrderringe": "J. R. R. Tolkien"}, artists={"walrusaudioeffects": "The Oh Hellos"})
    gs = build_groups(tracks(), mem)
    assert group(gs, "Herr der Ringe").albumartist == "J. R. R. Tolkien"
    assert group(gs, "Live _ Sessions").sure


def test_target_paths():
    assert target_relpath(MUSIK, "Folk!", "Rend Collective", "Burn", None) == "music/Rend Collective/Folk!/Burn.mp3"
    assert target_relpath(MUSIK, "Mountainkind Hymnal", "Mountainkind", "The Love of God", 2) == \
        "music/Mountainkind/Mountainkind Hymnal/02 - The Love of God.mp3"
    assert target_relpath(MUSIK, "A/B: C?", "AC/DC", "x", None) == "music/AC-DC/A-B - C/x.mp3"
    assert target_relpath(HOERBUCH, "Oliver Twist", "Charles Dickens", "Oliver Twist - Teil 1", 1, "Sven Görtz", "Hörbuch") == \
        "audiobooks/Charles Dickens/Oliver Twist {Sven Görtz}/Oliver Twist - Teil 1.mp3"
    assert target_relpath(HOERBUCH, "Oliver Twist", "Charles Dickens", "Oliver Twist", None, "", "Hörspiel") == \
        "audiobooks/Charles Dickens/Oliver Twist (Hörspiel)/Oliver Twist.mp3"
