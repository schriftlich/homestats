import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import APIC, COMM, ID3, TCON, TDRC, TIT2, TPE1, TXXX
from PIL import Image

FIX = Path(__file__).parent / "fixtures"


def make_mp3(path: Path, title: str, artist: str, genre="Music", date="2020", cover=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / "silence.mp3", path)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=[title]))
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TCON(encoding=3, text=[genre]))
    tags.add(TDRC(encoding=3, text=[date]))
    tags.add(TXXX(encoding=3, desc="description", text=["Lyrics und Links …"]))
    tags.add(TXXX(encoding=3, desc="synopsis", text=["Lyrics und Links …"]))
    tags.add(TXXX(encoding=3, desc="purl", text=["https://www.youtube.com/watch?v=x"]))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=["https://www.youtube.com/watch?v=x"]))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=(FIX / "thumb.jpg").read_bytes()))
    tags.save(path, v2_version=4)
    old = time.time() - 3600
    os.utime(path, (old, old))


@pytest.fixture
def env(tmp_path, monkeypatch):
    dl = tmp_path / "downloads"
    data = tmp_path / "data"
    monkeypatch.setenv("TAGGER_DOWNLOADS", str(dl))
    monkeypatch.setenv("TAGGER_DATA_DIR", str(data))
    monkeypatch.setenv("TAGGER_NO_WORKER", "1")
    from tagger import library
    library._cache.clear()
    inbox = dl / "metube"
    album = inbox / "Rend Collective - GOOD NEWS (Official Album Playlist)"
    make_mp3(album / "Rend Collective - Yahweh (Audio).mp3", "Rend Collective - Yahweh (Audio)", "Rend Collective", date="2018")
    make_mp3(album / "Rend Collective - True North (Audio).mp3", "Rend Collective - True North (Audio)", "Rend Collective", date="2018")
    make_mp3(album / "Rend Collective - Rescuer (Good News) [Official Music Video].mp3",
             "Rend Collective - Rescuer (Good News) [Official Music Video]", "Rend Collective", date="2017")
    make_mp3(inbox / "Herr der Ringe Hörspiel komplett deutsch.mp3", "Herr der Ringe Hörspiel komplett deutsch",
             "Jan is your Man", genre="Film & Animation", date="2026")
    make_mp3(inbox / "Songs at the Shop： Episode 4.mp3", "Songs at the Shop: Episode 4", "walrusaudioeffects")
    (inbox / ".metube").mkdir()
    (inbox / ".metube" / "queue.json").write_text("{}")
    (inbox / "läuft noch.mp3.part").write_bytes(b"x")
    return dl


@pytest.fixture
def client(env):
    from tagger.main import app

    with TestClient(app) as c:
        yield c


def gid_for(client, text):
    from tagger import library
    box = library.load_inbox()
    return next(g.id for g in box.groups if text in (g.folder or g.tracks[0].relpath))


def form_for(client, gid, **override):
    """Formular so absenden, wie der Browser es mit den Vorschlägen tun würde."""
    from tagger import library
    box = library.load_inbox()
    g = next(g for g in box.groups if g.id == gid)
    data = {"kind": g.kind, "album": g.album, "albumartist": g.albumartist, "year": g.year, "genre": g.genre,
            "narrator": g.narrator, "target": "move"}
    for t in g.tracks:
        p = g.proposals[t.id]
        data.update({f"on-{t.id}": "1", f"title-{t.id}": p.title, f"artist-{t.id}": p.artist,
                     f"track-{t.id}": p.track or ""})
    data.update(override)
    return g, data


def test_inbox_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Good News" in r.text and "Herr der Ringe" in r.text
    assert "queue.json" not in r.text and "läuft noch" not in r.text
    assert client.get("/healthz").json()["ok"]


def test_apply_album_and_undo(client, env):
    gid = gid_for(client, "GOOD NEWS")
    r = client.get(f"/gruppe/{gid}")
    assert r.status_code == 200
    assert "music/Rend Collective/Good News/Yahweh.mp3" in r.text

    g, data = form_for(client, gid, genre="Worship", action="apply")
    r = client.post(f"/gruppe/{gid}", data=data, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]

    dest = env / "music" / "Rend Collective" / "Good News"
    files = sorted(p.name for p in dest.iterdir())
    assert files == ["Rescuer (Good News).mp3", "True North.mp3", "Yahweh.mp3"]
    tags = ID3(dest / "Yahweh.mp3")
    assert tags.version == (2, 3, 0)
    assert str(tags["TIT2"]) == "Yahweh" and str(tags["TPE1"]) == "Rend Collective"
    assert str(tags["TPE2"]) == "Rend Collective" and str(tags["TALB"]) == "Good News"
    assert str(tags["TCON"]) == "Worship" and str(tags["TDRC"]) == "2018"   # ein Jahr fürs ganze Album
    assert "TXXX:description" not in tags and "TXXX:purl" in tags and tags.getall("COMM")
    cover = Image.open(__import__("io").BytesIO(tags.getall("APIC")[0].data))
    assert cover.size[0] == cover.size[1]
    # Leerer Album-Ordner im Eingang ist weg
    assert not (env / "metube" / "Rend Collective - GOOD NEWS (Official Album Playlist)").exists()

    entry = client.get("/verlauf")
    assert "Rend Collective – Good News" in entry.text
    from tagger import library
    eid = library.history()[0]["id"]
    r = client.post(f"/verlauf/{eid}/rueckgaengig", follow_redirects=False)
    assert "msg=" in r.headers["location"]
    back = env / "metube" / "Rend Collective - GOOD NEWS (Official Album Playlist)" / "Rend Collective - Yahweh (Audio).mp3"
    assert back.exists() and not (env / "music" / "Rend Collective").exists()
    tags = ID3(back)
    assert str(tags["TIT2"]) == "Rend Collective - Yahweh (Audio)" and "TXXX:description" in tags
    assert "TXXX:HOMESTATS_TAGGER" not in tags


def test_audiobook_needs_author_and_is_remembered(client, env):
    gid = gid_for(client, "Herr der Ringe")
    # kurze Testdatei -> wird als Musik erkannt; umstellen wie im Browser
    client.post(f"/gruppe/{gid}", data={"action": "as-hoerbuch"})
    g, data = form_for(client, gid, action="apply")
    assert g.kind == "hoerbuch" and g.album == "Herr der Ringe" and g.genre == "Hörspiel"
    r = client.post(f"/gruppe/{gid}", data=data)
    assert "Autor" in r.text and r.status_code == 200     # ohne Autor kein Übernehmen

    data["albumartist"] = "J. R. R. Tolkien"
    r = client.post(f"/gruppe/{gid}", data=data, follow_redirects=False)
    assert r.status_code == 303
    f = env / "audiobooks" / "J. R. R. Tolkien" / "Herr der Ringe (Hörspiel)" / "Herr der Ringe.mp3"
    assert f.exists()
    tags = ID3(f)
    assert str(tags["TPE1"]) == "J. R. R. Tolkien" and str(tags["TALB"]) == "Herr der Ringe"
    assert str(tags["TCON"]) == "Hörspiel"

    from tagger import library
    mem, _ = library.load_memory()
    assert mem.authors["herrderringe"] == "J. R. R. Tolkien"


def test_keep_in_inbox_and_artist_learning(client, env):
    gid = gid_for(client, "Songs at the Shop")
    g, data = form_for(client, gid, albumartist="The Oh Hellos", target="here", action="apply")
    data[f"artist-{g.tracks[0].id}"] = "The Oh Hellos"
    r = client.post(f"/gruppe/{gid}", data=data, follow_redirects=False)
    assert r.status_code == 303
    f = env / "metube" / "Songs at the Shop - Episode 4.mp3"
    assert f.exists() and "TXXX:HOMESTATS_TAGGER" in ID3(f)
    # bereits getaggt -> taucht nicht mehr als offen auf
    assert "Songs at the Shop" not in client.get("/").text.split("Eingang</h2>")[1]
    from tagger import library
    mem, _ = library.load_memory()
    assert mem.artists["walrusaudioeffects"] == "The Oh Hellos"


def test_apply_all_and_auto(client, env):
    from tagger import library
    r = client.post("/alle", follow_redirects=False)
    assert r.status_code == 303
    assert (env / "music" / "Rend Collective" / "Good News" / "Yahweh.mp3").exists()
    # Unsicheres bleibt liegen
    assert (env / "metube" / "Herr der Ringe Hörspiel komplett deutsch.mp3").exists()

    make_mp3(env / "metube" / "Boreas" / "The Oh Hellos - Rose.mp3", "The Oh Hellos - Rose", "The Oh Hellos")
    young = env / "metube" / "Boreas" / "The Oh Hellos - Cold.mp3"
    make_mp3(young, "The Oh Hellos - Cold", "The Oh Hellos")
    os.utime(young, None)   # gerade erst geladen -> Ordner noch nicht anfassen
    assert library.auto_run() == []
    old = time.time() - 3600
    os.utime(young, (old, old))
    res = library.auto_run()
    assert len(res) == 1 and res[0]["auto"]
    assert (env / "music" / "The Oh Hellos" / "Boreas" / "Rose.mp3").exists()


def test_ignore_and_settings(client, env):
    gid = gid_for(client, "Herr der Ringe")
    client.post(f"/gruppe/{gid}", data={"action": "ignore"})
    assert "Herr der Ringe" not in client.get("/").text
    page = client.get("/einstellungen").text
    assert "Herr der Ringe" in page and "Downloads/metube" in page
    client.post("/einstellungen/einblenden", data={"path": "Herr der Ringe Hörspiel komplett deutsch.mp3"})
    assert "Herr der Ringe" in client.get("/").text

    client.post("/einstellungen", data={"square_cover": "1", "auto": "1", "auto_minutes": "2"})
    from tagger import library
    s = library.load_settings()
    assert (s.move, s.auto, s.auto_minutes, s.strip_description) == (False, True, 5, False)


def test_cover_and_cross_site_post(client, env):
    gid = gid_for(client, "GOOD NEWS")
    from tagger import library
    box = library.load_inbox()
    tid = next(g for g in box.groups if g.id == gid).tracks[0].id
    r = client.get(f"/cover/{tid}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = client.post("/alle", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
