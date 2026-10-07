"""PDF der Mitgliederliste (ReportLab, A4 hoch).

In die Liste kommen nur Personen, die nicht ausgetreten sind und eine
Einwilligung haben. Ein Haushalt erscheint, sobald mindestens eine Person
darin aufgenommen wird.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, FrameBreak, KeepTogether, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from xml.sax.saxutils import escape

from .db import Household, Person
from .fmt import MONTHS_LONG, ddate

INK = colors.HexColor("#1a1a19")
MUTED = colors.HexColor("#6b6a65")
LINE = colors.HexColor("#d9d8d2")
TINT = colors.HexColor("#efeeea")

MARGIN_X = 16 * mm
CONTENT_W = A4[0] - 2 * MARGIN_X

S_TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK)
S_SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=10.5, leading=14, textColor=MUTED)
S_HEAD = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK)
S_ADDR = ParagraphStyle("addr", fontName="Helvetica", fontSize=9, leading=12, textColor=MUTED)
S_ADDR_R = ParagraphStyle("addr_r", parent=S_ADDR, alignment=TA_RIGHT)
S_CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=9, leading=11.5, textColor=INK)
S_CELL_B = ParagraphStyle("cellb", parent=S_CELL, fontName="Helvetica-Bold")
S_SMALL = ParagraphStyle("small", parent=S_CELL, fontSize=8, textColor=MUTED)
S_MONTH = ParagraphStyle("month", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK,
                         spaceBefore=6)


@dataclass
class Options:
    church_name: str = ""
    note: str = ""
    email: bool = True
    birthdays: bool = True
    stand: date | None = None


def listed_persons(h: Household) -> list[Person]:
    return [p for p in h.persons if p.active and p.consent]


def listed_households(households: list[Household]) -> list[Household]:
    return [h for h in households if listed_persons(h)]


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text or ""), style)


def _household_block(h: Household, opt: Options) -> KeepTogether:
    persons = listed_persons(h)
    if opt.email:
        widths = [60 * mm, 23 * mm, 34 * mm, 0, 17 * mm]
    else:
        widths = [80 * mm, 26 * mm, 40 * mm, 0]
    widths[3] = CONTENT_W - sum(widths)

    addr = ", ".join(x for x in (h.street, h.place) if x)
    left = f"<b>{escape(h.name)}</b>"
    if addr:
        left += f'&nbsp;&nbsp;&nbsp;<font name="Helvetica" size="9" color="#6b6a65">{escape(addr)}</font>'
    head = Table(
        [[Paragraph(left, S_HEAD), _p(f"Tel. {h.phone}" if h.phone else "", S_ADDR_R)]],
        colWidths=[(CONTENT_W - 12) * 0.7, (CONTENT_W - 12) * 0.3],
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    rows: list[list] = [[head] + [""] * (len(widths) - 1)]
    for p in persons:
        row = [
            _p(h.full_name(p), S_CELL_B if p.member else S_CELL),
            _p(ddate(p.birthday), S_CELL),
            _p(p.mobile, S_CELL),
        ]
        if opt.email:
            row += [_p(p.email, S_CELL), _p("Mitglied" if p.member else "", S_SMALL)]
        else:
            row += [_p("Mitglied" if p.member else "", S_SMALL)]
        rows.append(row)

    t = Table(rows, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), TINT),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, LINE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
    ]))
    return KeepTogether([t, Spacer(1, 3.5 * mm)])


TOP_MARGIN = 16 * mm
BOTTOM_MARGIN = 18 * mm
COL_GAP = 8 * mm
COL_W = (CONTENT_W - COL_GAP) / 2
TITLE_H = 14 * mm


def _page_templates() -> list[PageTemplate]:
    """Liste einspaltig; Geburtstage zweispaltig – ReportLab füllt erst die
    linke, dann die rechte Spalte und bricht erst um, wenn beide voll sind."""
    w, h = A4
    body_h = h - TOP_MARGIN - BOTTOM_MARGIN

    def cols(top_offset: float, prefix: str) -> list[Frame]:
        return [
            Frame(MARGIN_X + i * (COL_W + COL_GAP), BOTTOM_MARGIN, COL_W, body_h - top_offset,
                  id=f"{prefix}{i}", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
            for i in range(2)
        ]

    title = Frame(MARGIN_X, h - TOP_MARGIN - TITLE_H, CONTENT_W, TITLE_H, id="title",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    return [
        PageTemplate("list", [Frame(MARGIN_X, BOTTOM_MARGIN, CONTENT_W, body_h, id="list",
                                    leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)]),
        PageTemplate("cols_first", [title, *cols(TITLE_H, "f")]),
        PageTemplate("cols", cols(0, "c")),
    ]


def _birthday_section(households: list[Household]) -> list:
    entries: dict[int, list[tuple[date, str]]] = {m: [] for m in range(1, 13)}
    for h in households:
        for p in listed_persons(h):
            if p.birthday:
                entries[p.birthday.month].append((p.birthday, h.full_name(p)))

    widths = [13 * mm, COL_W - 24 * mm, 11 * mm]
    flow: list = [
        NextPageTemplate("cols_first"), PageBreak(),
        Paragraph("Geburtstage", S_TITLE), FrameBreak(),
        NextPageTemplate("cols"),
    ]
    for m in range(1, 13):
        items = sorted(entries[m], key=lambda e: (e[0].day, e[1]))
        if not items:
            continue
        rows = [[Paragraph(escape(MONTHS_LONG[m - 1]), S_MONTH), "", ""]]
        rows += [[_p(f"{b.day:02d}.{b.month:02d}.", S_CELL), _p(name, S_CELL), _p(str(b.year), S_SMALL)]
                 for b, name in items]
        # Läuft ein Monat in die nächste Spalte weiter, wird die Überschrift wiederholt.
        t = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
        t.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)),
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ]))
        flow.append(t)
    return flow


class _Canvas(rl_canvas.Canvas):
    """Zweiter Durchlauf für „Seite x von y“."""

    def __init__(self, *args, header: str = "", note: str = "", **kw):
        super().__init__(*args, **kw)
        self._pages: list[dict] = []
        self._header = header
        self._note = note

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int):
        w, h = A4
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(MUTED)
        if self._pageNumber > 1:
            self.drawString(MARGIN_X, h - 11 * mm, self._header)
        self.setStrokeColor(LINE)
        self.setLineWidth(0.4)
        self.line(MARGIN_X, 13 * mm, w - MARGIN_X, 13 * mm)
        self.drawString(MARGIN_X, 9 * mm, self._note[:150])
        self.drawRightString(w - MARGIN_X, 9 * mm, f"Seite {self._pageNumber} von {total}")
        self.restoreState()


def render(households: list[Household], opt: Options) -> bytes:
    stand = opt.stand or date.today()
    listed = listed_households(households)
    persons = [p for h in listed for p in listed_persons(h)]
    members = sum(p.member for p in persons)

    title = "Mitgliederliste"
    header = " · ".join(x for x in (f"{title} {opt.church_name}".strip(), f"Stand {ddate(stand)}") if x)

    flow: list = [
        Paragraph(escape(title), S_TITLE),
        Paragraph(escape(opt.church_name), S_SUB) if opt.church_name else Spacer(1, 0),
        Paragraph(
            f"Stand {ddate(stand)} · {len(listed)} Haushalte · {len(persons)} Personen,"
            f" davon {members} Mitglieder · <b>fett</b> = Mitglied", S_SUB,
        ),
        Spacer(1, 6 * mm),
    ]
    if not listed:
        flow.append(_p("Keine Einträge mit Einwilligung vorhanden.", S_CELL))
    for h in listed:
        flow.append(_household_block(h, opt))

    if opt.birthdays and any(p.birthday for p in persons):
        flow += _birthday_section(listed)

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN, pageTemplates=_page_templates(),
        title=f"{title} {opt.church_name}".strip(), author=opt.church_name or "Gemeinde",
    )
    doc.build(flow, canvasmaker=lambda *a, **k: _Canvas(*a, header=header, note=opt.note, **k))
    return buf.getvalue()
