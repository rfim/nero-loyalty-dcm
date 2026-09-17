"""Shared PDF/Excel report-building utilities for the governance apps.

Both cost_report.py and security_report.py generate a formal PDF (via
reportlab) and a multi-sheet Excel workbook (via xlsxwriter) from the same
dataframes driving their on-screen dashboard. This module holds the parts
that are genuinely identical between the two: house style (colors, fonts,
page chrome) and the generic table/section builders. Report *content*
(which sections, which tables) stays in each app.
"""
import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable,
)

# ---- house style, shared with the on-screen HTML templates ----
INK = colors.HexColor("#1c2b3a")
INK_SECONDARY = colors.HexColor("#4c5c6b")
MUTED = colors.HexColor("#8a97a3")
RULE = colors.HexColor("#d7dde2")
ACCENT = colors.HexColor("#3d5a73")
STATUS_GOOD = colors.HexColor("#0ca30c")
STATUS_WARN = colors.HexColor("#b8860b")
STATUS_SERIOUS = colors.HexColor("#c56a3e")
STATUS_CRIT = colors.HexColor("#b23a3a")
TABLE_HEADER_BG = colors.HexColor("#eef1f4")

_styles = getSampleStyleSheet()
STYLE_TITLE = ParagraphStyle("ReportTitle", parent=_styles["Title"], fontName="Helvetica-Bold",
                              fontSize=20, textColor=INK, spaceAfter=2)
STYLE_SUBTITLE = ParagraphStyle("ReportSubtitle", parent=_styles["Normal"], fontName="Helvetica",
                                 fontSize=10.5, textColor=INK_SECONDARY, spaceAfter=0)
STYLE_META = ParagraphStyle("ReportMeta", parent=_styles["Normal"], fontName="Helvetica",
                             fontSize=8.5, textColor=MUTED)
STYLE_H1 = ParagraphStyle("H1", parent=_styles["Heading1"], fontName="Helvetica-Bold",
                           fontSize=13.5, textColor=INK, spaceBefore=16, spaceAfter=6)
STYLE_H2 = ParagraphStyle("H2", parent=_styles["Heading2"], fontName="Helvetica-Bold",
                           fontSize=11, textColor=ACCENT, spaceBefore=10, spaceAfter=4)
STYLE_BODY = ParagraphStyle("Body", parent=_styles["Normal"], fontName="Helvetica",
                             fontSize=9.5, textColor=INK, leading=13.5)
STYLE_CAPTION = ParagraphStyle("Caption", parent=_styles["Normal"], fontName="Helvetica-Oblique",
                                fontSize=8, textColor=MUTED, spaceBefore=2, spaceAfter=8)


def _footer(canvas, doc, report_title: str):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm, report_title)
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(
    report_title: str,
    report_subtitle: str,
    prepared_for: str,
    sections: list[tuple[str, list]],
) -> bytes:
    """sections: list of (heading, list-of-flowables). Each section starts on
    the current flow (no forced page break) except where the caller inserts
    PageBreak() itself inside a section's flowable list."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=report_title,
    )
    story = [
        Paragraph(report_title, STYLE_TITLE),
        Paragraph(report_subtitle, STYLE_SUBTITLE),
        Spacer(1, 4),
        Paragraph(
            f"Prepared for {prepared_for} &middot; Generated "
            f"{datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}",
            STYLE_META,
        ),
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=1, color=RULE),
    ]
    for heading, flowables in sections:
        story.append(Paragraph(heading, STYLE_H1))
        story.extend(flowables)

    def _on_page(canvas, doc_):
        _footer(canvas, doc_, report_title)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()


def styled_table(header: list[str], rows: list[list], col_widths=None) -> Table:
    data = [header] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("GRID", (0, 0), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def body(text: str) -> Paragraph:
    return Paragraph(text, STYLE_BODY)


def h2(text: str) -> Paragraph:
    return Paragraph(text, STYLE_H2)


def caption(text: str) -> Paragraph:
    return Paragraph(text, STYLE_CAPTION)


# ---- Excel ----

EXCEL_HEADER_FORMAT = {
    "bold": True, "bg_color": "#EEF1F4", "border": 1, "border_color": "#D7DDE2",
    "font_color": "#1C2B3A", "font_size": 10,
}
EXCEL_TITLE_FORMAT = {"bold": True, "font_size": 14, "font_color": "#1C2B3A"}
EXCEL_META_FORMAT = {"italic": True, "font_size": 9, "font_color": "#8A97A3"}


def write_excel_workbook(title: str, subtitle: str, sheets: dict) -> bytes:
    """sheets: {sheet_name: pandas.DataFrame}. Writes one formatted sheet per
    entry, each with a title band, header row, and auto-fit-ish column widths."""
    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        workbook = writer.book
        title_fmt = workbook.add_format(EXCEL_TITLE_FORMAT)
        meta_fmt = workbook.add_format(EXCEL_META_FORMAT)
        header_fmt = workbook.add_format(EXCEL_HEADER_FORMAT)

        for sheet_name, df in sheets.items():
            safe_name = sheet_name[:31]
            df.to_excel(writer, sheet_name=safe_name, startrow=3, index=False, header=False)
            ws = writer.sheets[safe_name]
            ws.write(0, 0, title, title_fmt)
            ws.write(1, 0, subtitle, meta_fmt)
            ws.write(2, 0, f"Generated {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}", meta_fmt)
            for col_idx, col_name in enumerate(df.columns):
                ws.write(3, col_idx, col_name, header_fmt)
                width = max(11, min(48, int(df[col_name].astype(str).str.len().max() or 0) + 2, len(str(col_name)) + 2))
                ws.set_column(col_idx, col_idx, width)
            ws.freeze_panes(4, 0)
    return buf.getvalue()
