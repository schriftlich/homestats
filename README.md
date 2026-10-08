# HomeStats – eigene Apps für Umbrel

Dieses Repo ist ein **Umbrel Community App Store** mit vier Apps:

- **Zählerstände** – Zählerstände für Gas, Wasser und Strom erfassen und auswerten: Verbrauch pro Monat, Kosten, Vorjahresvergleich und eine Hochrechnung gegen deine Abschläge (Nachzahlung oder Guthaben).
- **Gemeindeverzeichnis** – Haushalte und Personen der Gemeinde pflegen, Mitgliederliste als PDF. Siehe [Gemeindeverzeichnis](#gemeindeverzeichnis).
- **Bank-Import** – Kontoauszug (CSV) hochladen, Vorschau prüfen, Buchungen direkt in Firefly III oder Sure übernehmen. Siehe [Bank-Import](#bank-import).
- **MP3-Tagger** – MeTube-Downloads sauber taggen und in Navidrome bzw. Audiobookshelf einsortieren. Siehe [MP3-Tagger](#mp3-tagger).

```
umbrel-app-store.yml              Store-ID "homestats"
homestats-zaehler/                Umbrel-Paket Zählerstände (Manifest, Compose, Icon)
app/                              Quellcode Zählerstände (FastAPI, SQLite, Jinja2/HTMX, Chart.js) + Dockerfile
homestats-gemeinde/               Umbrel-Paket Gemeindeverzeichnis
gemeinde/                         Quellcode Gemeindeverzeichnis (FastAPI, SQLite, ReportLab) + Dockerfile
homestats-bankimport/             Umbrel-Paket Bank-Import
bankimport/                       Quellcode Bank-Import (Flask) + Dockerfile
homestats-tagger/                 Umbrel-Paket MP3-Tagger
tagger/                           Quellcode MP3-Tagger (FastAPI, mutagen, Pillow) + Dockerfile
.github/workflows/                CI (Tests) und Releases (Multi-Arch-Images nach ghcr.io)
docker-compose.dev.yml            Zählerstände lokal starten mit Beispieldaten
```

## Lokal testen

Mit Docker:

```sh
docker compose -f docker-compose.dev.yml up --build
```

Dann <http://localhost:8743> öffnen. Ist die Datenbank leer, werden Beispieldaten angelegt (zwei Jahre, inkl. Zählerwechsel beim Wasser). Die Daten liegen in `./data-dev/`; zum Zurücksetzen den Ordner löschen.

Ohne Docker (Python 3.12):

```sh
cd app
pip install -r requirements-dev.txt
python -m pytest -q                                   # Tests
HOMESTATS_DEMO=1 uvicorn homestats.main:app --reload  # http://localhost:8000
```

## Einmalige Einrichtung

1. **Repo öffentlich stellen** (GitHub → Settings → General → Danger Zone → Change visibility). Umbrel lädt den Store ohne Anmeldung, und die nativen arm64-Runner für den Build sind nur für öffentliche Repos kostenlos.
2. **Erstes Release** erstellen – entweder auf GitHub unter **Releases → Draft a new release** (neuer Tag `v0.1.0`, Ziel `main`, Text = Release Notes, **Publish release**) oder per Terminal:
   ```sh
   git tag -a v0.1.0 -m "Erste Version"
   git push origin v0.1.0
   ```
   Die Action „Release“ baut das Image und schreibt Version und Image-Digest nach `main`.
3. **Image öffentlich stellen**: GitHub → dein Profil → Packages → `homestats-zaehler` → Package settings → Change visibility → Public. Das ist nur beim ersten Mal nötig.

Vor dem ersten Release lässt sich die App in Umbrel noch nicht installieren, weil das Image noch nicht existiert.

## In Umbrel einbinden

1. Umbrel öffnen → **App Store** → oben rechts **⋯** → **Community App Stores**.
2. URL eintragen: `https://github.com/schriftlich/homestats` → **Add**.
3. Im Store „HomeStats“ die App **Zählerstände** installieren und öffnen.

Die App ist durch die Umbrel-Anmeldung geschützt und braucht keinen eigenen Login.

## Update-Ablauf

1. Code ändern, committen, nach `main` pushen. Die CI führt die Tests aus.
2. Neue Version taggen – auf GitHub über **Releases → Draft a new release** (der Release-Text wird zu den Release Notes in Umbrel) oder per Terminal, dann ist die Tag-Nachricht der Release-Text, eine Zeile pro Punkt:
   ```sh
   git tag -a v0.2.0 -m "Version 0.2.0" -m "- Neue Funktion X
   - Fehler Y behoben"
   git push origin v0.2.0
   ```
3. Die Action testet, baut für amd64 und arm64, prüft das Multi-Arch-Manifest und committet danach `version` und `image: …@sha256:…` ins Umbrel-Paket auf `main`.
4. In Umbrel erscheint das Update, sobald der Store neu geladen wird.

`version` und `image` in `homestats-zaehler/` **nicht von Hand ändern**. Das macht die Action, damit Umbrel nie ein Update anbietet, dessen Image noch nicht fertig gebaut ist.

## Daten und Backup

- Datenbank auf dem Pi: `~/umbrel/app-data/homestats-zaehler/data/db/homestats.sqlite3`. Sie ist Teil der normalen Umbrel-Backups.
- In der App unter **Daten** gibt es Export und Import: eine ZIP-Datei mit einer CSV pro Tabelle (Semikolon, Dezimalkomma). Der Import ersetzt **alle** Daten.

## So wird gerechnet

- **Verteilung:** Der Verbrauch zwischen zwei Ablesungen wird gleichmäßig auf die Tage verteilt und dann auf Kalendermonate summiert. Monate, die nur teilweise abgedeckt sind, werden als „teilw.“ markiert.
- **Zählerwechsel:** Beim alten Zähler das Ausbaudatum setzen und den Endstand am Ausbautag erfassen. Den neuen Zähler mit Einbaudatum und Anfangsstand anlegen. Verbrauch entsteht nur aus Differenzen innerhalb eines Zählers, deshalb gibt es weder einen Sprung noch negativen Verbrauch.
- **Kosten je Tag:** Grundpreis ÷ Tage des Monats + Verbrauch × Arbeitspreis. Es gilt jeweils der Tarif, der an diesem Tag gültig ist, Preiswechsel werden dadurch anteilig gerechnet. Bei Gas: kWh = m³ × Zustandszahl × Brennwert.
- **Hochrechnung:** Gemessene Tage im Abrechnungszeitraum zählen mit dem echten Verbrauch. Für die übrigen Tage wird der Tagesschnitt desselben Kalendermonats aus den Vorjahren genommen. Ohne Vorjahr gilt der Durchschnitt, bei Gas gewichtet mit einem typischen Heizprofil. Dazu kommt die Summe der Abschläge, auf Wunsch auch nur 11 pro Jahr.
- **Plausibilität:** Die App warnt, wenn ein Stand kleiner ist als der vorige oder der Verbrauch mehr als ±50 % vom Erwartungswert abweicht. Das Speichern wird dabei nicht blockiert.

## Bank-Import

Kontoauszug als CSV hochladen → Vorschau → Buchungen landen direkt in Firefly III oder Sure (über deren REST-Schnittstellen, ohne Data Importer). Das Ziel wird unter **Einstellungen** gewählt.

**Sure:** In Sure unter Einstellungen → API-Schlüssel einen Schlüssel mit Lese- und Schreibrecht erstellen und in Bank-Import eintragen. Da Sure keine IBAN an Konten speichert, wird in Bank-Import pro Sure-Konto die IBAN eingetragen. Doppelte Importe verhindert Sure zusätzlich selbst über die externe ID (`source: bankimport`). Umbuchungen kann die Sure-Schnittstelle nicht direkt anlegen – sie werden als Aus-/Eingang gebucht; Sure erkennt passende Gegenbuchungen auf eigenen Konten in der Regel selbst als Transfer.

- Das eigene Konto wird an der IBAN in der Datei erkannt. Dafür muss das Konto in Firefly als Bestandskonto **mit IBAN** angelegt sein.
- Überweisungen auf andere eigene Konten (ebenfalls mit IBAN in Firefly) werden als Umbuchung angelegt.
- Schon vorhandene Buchungen (gleiches Datum, Betrag und Beschreibung oder gleiche externe ID) und vorgemerkte Umsätze werden übersprungen.
- Verwendungszwecke über 500 Zeichen werden gekürzt, der volle Text steht dann in der Notiz.
- Firefly-Regeln werden beim Import angewendet, jede Buchung bekommt das Schlagwort „Bank-Import“.
- Unterstützt bisher: DKB (neues Banking, CSV-Export). Weitere Banken: neuen Parser in `bankimport/bankimport/banks.py` ergänzen.

**Einrichtung in der App:** Firefly → Profil → Fernzugriff und Token → Persönliche Zugangstoken → neuen Token erstellen. In Bank-Import unter **Einstellungen** einfügen und „Verbindung testen“. Der Token liegt in `~/umbrel/app-data/homestats-bankimport/data/settings/settings.json`.

**Release:** wie oben, aber mit eigenem Tag-Präfix:
```sh
git tag -a bankimport-v0.2.0 -m "Version 0.2.0" -m "- Neue Funktion X"
git push origin bankimport-v0.2.0
```
Beim ersten Release das Package `homestats-bankimport` auf GitHub öffentlich stellen (Profil → Packages → Package settings → Change visibility → Public).

**Lokal testen:**
```sh
cd bankimport
pip install -r requirements-dev.txt
python -m pytest -q
```

## Gemeindeverzeichnis

Haushalte (Familienname, Anschrift, Festnetz) mit den Personen darin (Geburtstag, Handy, E-Mail, Mitglied ja/nein, Kind). Daraus entsteht per Klick die Mitgliederliste als PDF.

- **Einwilligung:** Nur Personen mit gesetztem Haken „Einwilligung liegt vor“ kommen ins PDF. Alle anderen bleiben intern gespeichert und werden unter *Liste* aufgeführt.
- **Austritt, Wegzug, Tod:** Datum bei der Person setzen statt löschen. Sie verschwindet aus Liste und Geburtstagen, die Historie bleibt.
- **Backup:** *Einstellungen → Backup herunterladen* (ZIP mit CSV). Enthält alle persönlichen Daten.
- **Karte:** Haushalte als Stecknadeln (Leaflet, lokal eingebunden; Kartenbilder von OpenStreetMap). Koordinaten werden beim Speichern über OpenStreetMap Nominatim ermittelt – übertragen werden nur Straße, PLZ und Ort, keine Namen. Ungenaue oder nicht gefundene Adressen lassen sich beim Haushalt per Klick/Ziehen korrigieren; von Hand gesetzte Positionen bleiben bis zu einem Umzug erhalten.
- Lokal testen: `cd gemeinde && GEMEINDE_DEMO=1 uvicorn gemeinde.main:app --reload` (legt Beispieldaten an).
- Release: Tag `gemeinde-v1.2.3` (Workflow „Release Gemeindeverzeichnis“).

## MP3-Tagger

Liest den MeTube-Ordner (`Downloads/metube`), schlägt saubere Tags vor und sortiert die Dateien ein:

- **Musik** → `Downloads/music/<Interpret>/<Album>/<Nr - >Titel.mp3` (Navidrome)
- **Hörbücher/Hörspiele** → `Downloads/audiobooks/<Autor>/<Buch {Sprecher}>/<Titel>.mp3` bzw. `<Buch (Hörspiel)>` (Audiobookshelf)
- Alternativ „Hier lassen“: nur Tags und Dateiname, die Datei bleibt im MeTube-Ordner.

So wird erkannt:

- Ein Ordner im Eingang (MeTube-Playlist) = ein Album. Albumname aus dem Ordnernamen ohne „(Official Album Playlist)“ o. Ä.
- Interpret/Titel aus dem YouTube-Titel („Interpret - Titel“, „Titel - Interpret“, „"Titel" from Interpret“, „Serie #3 - Titel“ → Nr. 3). Kanalnamen wie „Audiotree“ oder „NPR Music“ werden nicht zum Interpreten. Zusätze wie „(Official Audio)“, „[Lyric Video]“ fallen weg, GROSSSCHREIBUNG wird normalisiert.
- Hörbuch, wenn lang (≥ 45 Min. und keine YouTube-Kategorie „Music“) oder ≥ 20 Min. mit „Hörbuch/Hörspiel/gelesen von …“. Autor aus „Titel – Autor“ oder „nach Autor“, Sprecher aus „Gelesen von …“ / „… liest“.
- Ein Jahr pro Album (häufigstes), damit Navidrome Alben nicht aufteilt. Cover wird quadratisch zugeschnitten, Videobeschreibung entfernt (Link zum Video bleibt).
- **„sicher“** = Interpret aus dem Titel bestätigt (bzw. Autor bei Hörbüchern gefunden). Nur solche Gruppen übernehmen „Alles Sichere“ und die Automatik.
- Korrekturen werden gelernt: Kanal → Interpret, Buchtitel → Autor (`~/umbrel/app-data/homestats-tagger/data/memory.json`).
- **Verlauf → Rückgängig** stellt alte Tags (Sicherung unter `data/undo/`) und den alten Ort wieder her.
- **Automatik** (Einstellungen, standardmäßig aus): prüft alle X Minuten, übernimmt nur sichere Gruppen, deren Ordner seit 10 Minuten unverändert ist.

Die App hängt `~/umbrel/data/storage/downloads` als `/downloads` ein (wie MeTube, Navidrome, Audiobookshelf) und läuft als UID 1000.

- Lokal testen: `cd tagger && pip install -r requirements-dev.txt && python -m pytest -q` – die Erkennung wird gegen 236 echte MeTube-Titel geprüft (`tests/corpus.json`).
- Lokal starten: `TAGGER_DOWNLOADS=/pfad/zu/downloads TAGGER_DATA_DIR=./data-dev uvicorn tagger.main:app --reload`
- Release: Tag `tagger-v1.2.3` (Workflow „Release MP3-Tagger“). Beim ersten Release das Package `homestats-tagger` auf GitHub öffentlich stellen.
