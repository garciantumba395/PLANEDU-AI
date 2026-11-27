"""Exports XLSX (openpyxl), CSV et PDF (reportlab) ; neutralise l'injection de formules (OWASP CSV injection)."""
import csv
import io
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
LOGO = STATIC / "logo.png"          # PDF : dessiné en filigrane (opacité 9 %)
WATERMARK = STATIC / "watermark.png"  # XLSX : logo pré-estompé (10 %)

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
}


def _safe(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def csv_bytes(cols, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if v is None else _safe(v) for v in r])
    return ("﻿" + out.getvalue()).encode("utf-8")


def xlsx_bytes(title, cols, rows, meta):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Données"
    ws.append([title])
    ws.append([meta])
    ws.append([])
    ws.append(list(cols))
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"].font = Font(italic=True, color="9C2B1B")
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
    for r in rows:
        ws.append([_safe(v) for v in r])
    for i, col in enumerate(cols, start=1):
        width = max([len(str(col))] + [len(str(r[i - 1])) for r in rows[:200] if i - 1 < len(r) and r[i - 1] is not None])
        ws.column_dimensions[ws.cell(row=4, column=i).column_letter].width = min(60, width + 2)
    ws.freeze_panes = "A5"
    try:  # filigrane : logo estompé flottant derrière/au-dessus des données, centré sur la zone visible
        from openpyxl.drawing.image import Image as XlImage
        img = XlImage(str(WATERMARK))
        img.width, img.height = 520, int(520 * img.height / img.width)
        ws.add_image(img, "B6")
        ws.oddHeader.center.text = "PLANEDU-IA - Données de démonstration"
    except Exception:
        pass  # le filigrane ne doit jamais bloquer un export
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def pdf_bytes(title, cols, rows, meta, max_rows=600):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    ss = getSampleStyleSheet()
    cell = ss["BodyText"].clone("cell", fontSize=6.5, leading=8)
    head = ss["BodyText"].clone("head", fontSize=7, leading=8, textColor=colors.white)
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=30, bottomMargin=28, title=title)

    from reportlab.pdfgen.canvas import Canvas

    class WMCanvas(Canvas):
        """Dessine le filigrane APRÈS le contenu de la page (sinon les fonds du tableau le masquent)."""

        def showPage(self):
            try:
                pw, ph = landscape(A4)
                w = 440
                self.saveState()
                self.setFillAlpha(0.10)
                self.drawImage(str(LOGO), (pw - w) / 2, (ph - w * 0.66) / 2, width=w, height=w * 0.66, mask="auto", preserveAspectRatio=True)
                self.restoreState()
            except Exception:
                pass  # le filigrane ne doit jamais bloquer un export
            super().showPage()

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#9C2B1B"))
        canvas.drawString(24, 16, "PLANEDU-IA - Données de démonstration (synthétiques) - aide à la décision, aucune décision automatique")
        canvas.drawRightString(landscape(A4)[0] - 24, 16, f"Page {d.page}")
        canvas.restoreState()

    story = [Paragraph(f"<b>{escape(title)}</b>", ss["Title"]),
             Paragraph(f"<font color='#9C2B1B'><b>{escape(meta)}</b></font>", ss["BodyText"]), Spacer(1, 8)]
    data = [[Paragraph(escape(str(c)), head) for c in cols]]
    for r in rows[:max_rows]:
        data.append([Paragraph(escape("" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))), cell) for v in r])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF3F8")]),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C4D0")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t)
    if len(rows) > max_rows:
        story += [Spacer(1, 6), Paragraph(f"{len(rows) - max_rows} ligne(s) supplémentaire(s) non affichée(s) : utiliser l'export XLSX ou CSV.", ss["BodyText"])]
    doc.build(story, onFirstPage=footer, onLaterPages=footer, canvasmaker=WMCanvas)
    return bio.getvalue()
