"""Bank-Import: Kontoauszüge (CSV) per Upload direkt in Firefly III übernehmen."""
import os
import threading
import time
import uuid

from flask import Flask, abort, redirect, render_template_string, request, url_for

from . import settings
from .banks import ParseError, parse
from .firefly import Firefly, FireflyError, build_plan, run_import

VERSION = os.environ.get("BANKIMPORT_VERSION", "dev")
TAG = os.environ.get("IMPORT_TAG", "Bank-Import")

DEFAULT_CATEGORIES = [
    "Wohnen", "Energie", "Lebensmittel", "Haushalt", "Mobilität", "Kommunikation",
    "Versicherungen", "Kinder", "Gesundheit", "Gemeinde & Spenden", "Freizeit & Urlaub",
    "Abos & Software", "Einkommen", "Gebühren & Zinsen",
]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

PLANS = {}  # Vorschau-ID -> (Zeitstempel, Plan)
LOCK = threading.Lock()


class NotConfigured(Exception):
    pass


def ff() -> Firefly:
    cfg = settings.load()
    if not cfg["firefly_token"]:
        raise NotConfigured()
    return Firefly(cfg["firefly_url"], cfg["firefly_token"])


def firefly_link() -> str:
    link = settings.load()["firefly_link"]
    if link:
        return link
    host = request.host.split(":")[0]
    return f"{request.scheme}://{host}:30009"


def _cleanup():
    cutoff = time.time() - 3600
    for k in [k for k, (t, _) in PLANS.items() if t < cutoff]:
        PLANS.pop(k, None)


@app.before_request
def block_cross_site_posts():
    if request.method == "POST" and request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)


BASE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bank-Import</title>
<link rel="icon" href="{{ url_for('static_icon') }}" type="image/svg+xml">
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1d232b;--muted:#667085;--line:#e4e7ec;
--accent:#1b6e5a;--accent-text:#fff;--neu:#e7f4ec;--neu-t:#1f6b3d;--dup:#eef0f3;--dup-t:#56606e;
--vor:#fff4e0;--vor-t:#8a5a00;--err:#fdecec;--err-t:#a32020;--ok:#e7f4ec;--ok-t:#1f6b3d;--out:#a32020;--in:#1f6b3d}
@media (prefers-color-scheme: dark){:root{--bg:#14171c;--card:#1d222a;--text:#e6e9ee;--muted:#9aa3af;
--line:#2c333d;--accent:#4fb596;--accent-text:#08201a;--neu:#173324;--neu-t:#8fdcae;--dup:#252b34;--dup-t:#a4adba;
--vor:#3a2d10;--vor-t:#f0c46a;--err:#3d1a1a;--err-t:#f19a9a;--ok:#173324;--ok-t:#8fdcae;--out:#f19a9a;--in:#8fdcae}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line)}
.bar{max-width:980px;margin:0 auto;padding:12px 16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:17px;color:var(--text);text-decoration:none;margin-right:auto}
.brand img{width:28px;height:28px}
nav a{color:var(--muted);text-decoration:none;font-weight:600;padding:6px 2px}
nav a.on{color:var(--text);border-bottom:2px solid var(--accent)}
main{max-width:980px;margin:0 auto;padding:24px 16px 60px}
h2{font-size:18px;margin:0 0 12px}
.sub{color:var(--muted);margin:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:18px}
.drop{border:2px dashed var(--line);border-radius:10px;padding:28px;text-align:center}
input[type=file]{font-size:15px;max-width:100%}
label{display:block;font-weight:600;margin:14px 0 6px}
input[type=text],input[type=password]{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--text);font-size:15px}
.hint{color:var(--muted);font-size:13px;margin-top:4px}
button,.btn{background:var(--accent);color:var(--accent-text);border:0;border-radius:8px;
padding:10px 18px;font-size:15px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
.btn.sec,button.sec{background:transparent;color:var(--text);border:1px solid var(--line)}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.pill{border-radius:999px;padding:3px 10px;font-size:13px;font-weight:600;white-space:nowrap}
.neu{background:var(--neu);color:var(--neu-t)}.duplikat{background:var(--dup);color:var(--dup-t)}
.vorgemerkt{background:var(--vor);color:var(--vor-t)}.fehler{background:var(--err);color:var(--err-t)}
.tbl{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:13px}
td.amt{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.out{color:var(--out)}.in{color:var(--in)}
.desc{color:var(--muted);font-size:13px;max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.alert{background:var(--err);color:var(--err-t);border-radius:10px;padding:14px 16px;margin-bottom:18px}
.alert.ok{background:var(--ok);color:var(--ok-t)}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
tr.dim td{opacity:.55}
select{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text);font-size:14px;max-width:160px}
.learned{display:block;color:var(--muted);font-size:12px;margin-top:2px}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:30px}
</style></head><body>
<header><div class="bar">
<a class="brand" href="{{ url_for('index') }}"><img src="{{ url_for('static_icon') }}" alt="">Bank-Import</a>
<nav><a href="{{ url_for('index') }}" class="{{ 'on' if active=='import' }}">Import</a>
&nbsp;&nbsp;<a href="{{ url_for('settings_page') }}" class="{{ 'on' if active=='settings' }}">Einstellungen</a></nav>
</div></header>
<main>
{% if error %}<div class="alert">{{ error }}</div>{% endif %}
{% if notice %}<div class="alert ok">{{ notice }}</div>{% endif %}
{{ body|safe }}
<footer>Bank-Import {{ version }}</footer>
</main></body></html>"""

UPLOAD = """<div class="card"><h2>Kontoauszug hochladen</h2>
<form method="post" action="{{ url_for('preview') }}" enctype="multipart/form-data">
<div class="drop"><input type="file" name="file" accept=".csv,text/csv" required></div>
<div class="actions"><button type="submit">Vorschau anzeigen</button></div></form>
<p class="sub" style="margin-top:14px">Unterstützt: DKB-Umsatzliste (CSV-Export aus dem Banking).
Das Konto wird an der IBAN erkannt, bereits importierte Buchungen werden übersprungen.</p></div>"""

SETUP = """<div class="card"><h2>Einrichtung</h2>
<p>Bank-Import braucht einen Zugangsschlüssel für Firefly III, bevor es losgehen kann.</p>
<div class="actions"><a class="btn" href="{{ url_for('settings_page') }}">Zu den Einstellungen</a></div></div>"""

SETTINGS = """<div class="card"><h2>Verbindung zu Firefly III</h2>
<form method="post" action="{{ url_for('settings_page') }}">
<label for="token">Persönlicher Zugangsschlüssel</label>
<input type="password" id="token" name="firefly_token" autocomplete="off"
 placeholder="{{ 'Gespeichert – leer lassen, um ihn zu behalten' if has_token else 'Token hier einfügen' }}">
<div class="hint">In Firefly: Profil → Fernzugriff und Token → Persönliche Zugangstoken → Neuen Token erstellen.</div>
<label for="url">Interne Adresse von Firefly</label>
<input type="text" id="url" name="firefly_url" value="{{ cfg.firefly_url }}">
<div class="hint">Auf Umbrel normalerweise unverändert lassen.</div>
<label for="link">Adresse für „Firefly öffnen“ (optional)</label>
<input type="text" id="link" name="firefly_link" value="{{ cfg.firefly_link }}" placeholder="automatisch: gleicher Rechner, Port 30009">
<div class="actions"><button type="submit" name="action" value="save">Speichern</button>
<button class="sec" type="submit" name="action" value="test">Verbindung testen</button></div>
</form></div>
{% if accounts is not none %}<div class="card"><h2>Bestandskonten mit IBAN in Firefly</h2>
{% if accounts %}<div class="tbl"><table><tr><th>Konto</th><th>IBAN</th></tr>
{% for iban, (id, name) in accounts.items() %}<tr><td>{{ name }}</td><td>{{ iban }}</td></tr>{% endfor %}
</table></div>{% else %}<p class="sub">Keine Bestandskonten mit IBAN gefunden. Trage bei deinen Konten in Firefly die IBAN ein – daran erkennt Bank-Import, wohin gebucht wird.</p>{% endif %}
</div>{% endif %}
<div class="card"><h2>Kategorien</h2>
<p class="sub">Legt in Firefly die Standard-Kategorien an, die noch fehlen. Bestehende bleiben unverändert.</p>
<p class="sub" style="margin-top:8px">{{ default_categories|join(" · ") }}</p>
<form method="post" action="{{ url_for('create_categories') }}"><div class="actions">
<button class="sec" type="submit">Fehlende Kategorien anlegen</button></div></form></div>"""

PREVIEW = """<div class="card"><h2>Vorschau – {{ plan.account_name }} ({{ plan.statement.bank }})</h2>
<div class="stats">
<span class="pill neu">{{ n_new }} neu</span>
<span class="pill duplikat">{{ n_dup }} schon vorhanden</span>
{% if n_pend %}<span class="pill vorgemerkt">{{ n_pend }} vorgemerkt (werden übersprungen)</span>{% endif %}
</div>
{% if n_new and n_learned %}<p class="sub" style="margin-bottom:12px">{{ n_learned }} von {{ n_new }} neuen Buchungen haben einen Kategorie-Vorschlag aus deinen bisherigen Buchungen.</p>{% endif %}
<form method="post" action="{{ url_for('do_import', pid=pid) }}">
<div class="tbl"><table><tr><th>Datum</th><th>Gegenpartei / Zweck</th><th>Art</th><th style="text-align:right">Betrag</th><th>Kategorie</th><th>Status</th></tr>
{% for r in plan.rows %}{% set i = loop.index0 %}
<tr class="{{ '' if r.status=='neu' else 'dim' }}">
<td>{{ r.booking.date.strftime('%d.%m.%Y') }}</td>
<td>{{ r.other }}<div class="desc" title="{{ r.booking.description }}">{{ r.booking.description }}</div></td>
<td>{{ {'withdrawal':'Ausgabe','deposit':'Einnahme','transfer':'Umbuchung'}[r.kind] }}</td>
<td class="amt {{ 'out' if r.booking.amount < 0 else 'in' }}">{{ fmt(r.booking.amount) }}</td>
<td>{% if r.status == 'neu' and r.kind != 'transfer' %}
<select name="cat_{{ i }}"><option value="">– keine –</option>
{% for c in categories %}<option value="{{ c }}" {{ 'selected' if c == r.category }}>{{ c }}</option>{% endfor %}
{% if r.category and r.category not in categories %}<option value="{{ r.category }}" selected>{{ r.category }}</option>{% endif %}
</select>{% if r.category %}<span class="learned">gelernt</span>{% endif %}
{% endif %}</td>
<td><span class="pill {{ r.status }}">{{ r.status }}</span></td></tr>
{% endfor %}</table></div>
<div class="actions">
{% if n_new %}<button type="submit">{{ n_new }} Buchung{{ 'en' if n_new != 1 }} importieren</button>{% endif %}
<a class="btn sec" href="{{ url_for('index') }}">Abbrechen</a></div></form></div>"""

RESULT = """<div class="card"><h2>Ergebnis</h2>
<div class="stats"><span class="pill neu">{{ ok }} importiert</span>
{% if fail %}<span class="pill fehler">{{ fail }} fehlgeschlagen</span>{% endif %}</div>
{% if fail %}<div class="tbl"><table><tr><th>Zeile</th><th>Buchung</th><th>Fehler</th></tr>
{% for r, good, info in results if not good %}
<tr><td>{{ r.booking.line }}</td><td>{{ r.booking.date.strftime('%d.%m.%Y') }} · {{ r.other }} · {{ fmt(r.booking.amount) }}</td><td>{{ info }}</td></tr>
{% endfor %}</table></div>{% endif %}
<div class="actions"><a class="btn" href="{{ link }}" target="_blank" rel="noopener">Firefly öffnen</a>
<a class="btn sec" href="{{ url_for('index') }}">Nächste Datei</a></div></div>"""


def fmt(amount):
    s = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def page(body_tpl, active="import", error=None, notice=None, **ctx):
    body = render_template_string(body_tpl, fmt=fmt, **ctx)
    return render_template_string(BASE, body=body, error=error, notice=notice,
                                  active=active, version=VERSION)


@app.get("/")
def index():
    if not settings.load()["firefly_token"]:
        return page(SETUP)
    return page(UPLOAD, error=request.args.get("error"))


@app.route("/einstellungen", methods=["GET", "POST"])
def settings_page():
    error = notice = None
    accounts = None
    if request.method == "POST":
        token = request.form.get("firefly_token", "").strip()
        values = {"firefly_url": request.form.get("firefly_url", "").strip() or settings.DEFAULT_URL,
                  "firefly_link": request.form.get("firefly_link", "").strip()}
        if token:
            values["firefly_token"] = token
        settings.save(**values)
        notice = "Gespeichert."
        if request.form.get("action") == "test" or token:
            try:
                accounts = ff().asset_accounts()
                notice = "Gespeichert – Verbindung zu Firefly funktioniert."
            except NotConfigured:
                error, notice = "Es ist noch kein Zugangsschlüssel gespeichert.", None
            except Exception as e:
                error, notice = f"Verbindung fehlgeschlagen: {e}", None
    cfg = settings.load()
    return page(SETTINGS, active="settings", error=error, notice=notice, cfg=cfg,
                has_token=bool(cfg["firefly_token"]), accounts=accounts,
                default_categories=DEFAULT_CATEGORIES)


@app.post("/einstellungen/kategorien")
def create_categories():
    cfg = settings.load()
    error = notice = None
    try:
        client = ff()
        existing = {c.lower() for c in client.categories()}
        missing = [c for c in DEFAULT_CATEGORIES if c.lower() not in existing]
        for name in missing:
            client.create_category(name)
        notice = (f"{len(missing)} Kategorien angelegt: {', '.join(missing)}." if missing
                  else "Alle Standard-Kategorien sind schon vorhanden.")
    except NotConfigured:
        error = "Bitte zuerst den Zugangsschlüssel speichern."
    except Exception as e:
        error = f"Kategorien konnten nicht angelegt werden: {e}"
    return page(SETTINGS, active="settings", error=error, notice=notice, cfg=cfg,
                has_token=bool(cfg["firefly_token"]), accounts=None,
                default_categories=DEFAULT_CATEGORIES)


@app.post("/vorschau")
def preview():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("index", error="Bitte eine Datei auswählen."))
    categories = []
    try:
        st = parse(f.read())
        client = ff()
        plan = build_plan(client, st, TAG)
        try:
            categories = client.categories()
        except Exception:
            categories = []
    except NotConfigured:
        return page(SETUP)
    except (ParseError, FireflyError) as e:
        return page(UPLOAD, error=str(e))
    except Exception as e:  # z. B. Firefly nicht erreichbar
        return page(UPLOAD, error=f"Unerwarteter Fehler: {e}")
    pid = uuid.uuid4().hex
    with LOCK:
        _cleanup()
        PLANS[pid] = (time.time(), plan)
    n = lambda s: sum(1 for r in plan.rows if r.status == s)  # noqa: E731
    n_learned = sum(1 for r in plan.rows if r.status == "neu" and r.category)
    return page(PREVIEW, plan=plan, pid=pid, n_new=n("neu"), n_dup=n("duplikat"),
                n_pend=n("vorgemerkt"), n_learned=n_learned, categories=categories)


@app.post("/import/<pid>")
def do_import(pid):
    with LOCK:
        item = PLANS.pop(pid, None)
    if not item:
        return redirect(url_for("index", error="Die Vorschau ist abgelaufen. Bitte die Datei erneut hochladen."))
    try:
        plan = item[1]
        choices = {i: request.form.get(f"cat_{i}", "").strip()
                   for i, r in enumerate(plan.rows) if r.status == "neu"}
        results = run_import(ff(), plan, choices)
    except Exception as e:
        return page(UPLOAD, error=f"Import abgebrochen: {e}")
    ok = sum(1 for _, good, _ in results if good)
    return page(RESULT, results=results, ok=ok, fail=len(results) - ok, link=firefly_link())


ICON = None


@app.get("/icon.svg")
def static_icon():
    global ICON
    if ICON is None:
        from pathlib import Path
        ICON = (Path(__file__).parent / "icon.svg").read_text()
    return ICON, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "max-age=86400"}


@app.get("/healthz")
def healthz():
    return {"ok": True}
