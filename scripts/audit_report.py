#!/usr/bin/env python3
"""Generate a clean, printable PDF audit report from a JSON report spec.

This is the choiceadvantage-guest-ledger-duplicate-audit skill's PDF
renderer. It is deterministic: build a JSON spec of the completed (fresh)
review, then run this script to emit a styled PDF.

Usage:
  python3 audit_report.py <spec.json> -o <report.pdf>
  python3 audit_report.py - -o <report.pdf>     # spec via stdin

Primary renderer is reportlab. If reportlab is not installed, the script
falls back to an inline-HTML document printed to PDF via headless Chromium.

Report spec schema (all fields optional unless noted otherwise):

{
  "title": str,                      # e.g. "Guest Ledger & Duplicate Reservation Audit"
  "property": str,                   # e.g. "CAF15 — Casa Via Mar Inn, Ascend Hotel Collection"
  "reviewed_at": str,                # owner-local date/time + zone
  "business_date": str,              # optional, ledger business date
  "date_ranges": { "previous_90": str, "next_90": str },   # optional
  "disclaimer": str,                 # read-only statement (recommended)
  "completion_warning": str | null,  # shown in red if the review was incomplete
  "sections": [
    {
      "heading": str,
      "body": [str, ...],                    # optional paragraphs
      "tables": [
        {
          "table_title": str,
          "headers": [str, ...],
          "rows": [[cell, ...], ...],        # floats render as currency; ints plain; str as-is
          "summary": str                     # optional bold total line
        }
      ],
      "notes": [str, ...]                    # optional bullet notes
    }
  ],
  "limitations": [str, ...]          # optional; rendered as a final section

Empty string / null cells render blank — pass "Not displayed." for missing
fields (the audit skill already requires this).
"""

import argparse
import html as _html
import json
import sys

MONEY_HEADER_TOKENS = {"balance", "amount", "total", "rate", "rooms", "night", "nights", "count", "sum", "$"}


def _money(v):
    neg = v < 0
    s = f"${abs(v):,.2f}"
    return f"({s})" if neg else s


def _cell_text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return _money(v)
    if isinstance(v, int):
        return str(v)
    return str(v)


def _esc(s):
    return _html.escape(s, quote=False).replace("\n", "<br/>")


def _money_cell(v):
    neg = v < 0
    return neg


# ---------------------------------------------------------------------------
# reportlab renderer (primary)
# ---------------------------------------------------------------------------
def render_reportlab(spec, out_path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    NAVY = colors.HexColor("#203F68")
    TEAL = colors.HexColor("#0B7477")
    LIGHT = colors.HexColor("#EEF3F8")
    BORDER = colors.HexColor("#C8D2DD")
    RED = colors.HexColor("#B00020")
    GRAY = colors.HexColor("#5A6472")

    PAGE_W, PAGE_H = letter
    MARGIN = 0.5 * inch
    USABLE = PAGE_W - 2 * MARGIN

    st_title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY)
    st_prop = ParagraphStyle("p", fontName="Helvetica", fontSize=11, leading=15, textColor=colors.HexColor("#333F4E"))
    st_h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13.5, leading=17, textColor=TEAL, spaceBefore=6)
    st_body = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=colors.black)
    st_cell = ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=11)
    st_cell_bold = ParagraphStyle("cb", fontName="Helvetica-Bold", fontSize=8.5, leading=11)
    st_note = ParagraphStyle("n", fontName="Helvetica", fontSize=8.5, leading=12, textColor=GRAY)
    st_summary = ParagraphStyle("s", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY)

    def _table(tspec):
        headers = tspec.get("headers", [])
        rows = tspec.get("rows", []) or [["None found"]]
        num_cols = len(headers)
        # normalize rows to num_cols
        norm_rows = []
        for r in rows:
            r = list(r)
            if len(r) < num_cols:
                r += [""] * (num_cols - len(r))
            norm_rows.append(r[:num_cols])

        # decide right-aligned columns
        right = [False] * num_cols
        for ci, h in enumerate(headers):
            hl = h.lower()
            if any(t in hl for t in MONEY_HEADER_TOKENS):
                right[ci] = True
        # column width heuristic from string lengths
        maxlen = [max([len(headers[ci])] + [len(_cell_text(r[ci])) for r in norm_rows]) for ci in range(num_cols)]
        total = sum(maxlen) or 1
        widths = [max(32.0, USABLE * (ml / total)) for ml in maxlen]
        # renormalize to usable width
        wtotal = sum(widths)
        widths = [w * USABLE / wtotal for w in widths]

        def cell(v, ci, bold=False):
            style = st_cell_bold if bold else st_cell
            txt = _cell_text(v)
            # highlight negative currency in red
            if isinstance(v, float) and v < 0:
                txt = f'<font color="#B00020">{_esc(_money(v))}</font>'
            else:
                txt = _esc(txt)
            align = 2 if right[ci] else 0  # 2 = right
            return Paragraph(txt, style)

        data = [[Paragraph(_esc(h), st_cell_bold) for h in headers]]
        for r in norm_rows:
            data.append([cell(r[ci], ci) for ci in range(num_cols)])

        tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 1), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ]
        for ri in range(1, len(data)):
            if ri % 2 == 0:
                style.append(("BACKGROUND", (0, ri), (-1, ri), LIGHT))
        for ci in range(num_cols):
            if right[ci]:
                style.append(("ALIGN", (ci, 1), (ci, -1), "RIGHT"))
        tbl.setStyle(TableStyle(style))
        return tbl

    def build_flowables():
        f = []
        # ------- header -------
        f.append(Paragraph(_esc(spec.get("title") or "Audit Report"), st_title))
        f.append(Spacer(1, 3))
        f.append(HRFlowable(width="100%", thickness=1.2, color=NAVY))
        f.append(Spacer(1, 6))
        meta = [("Property", spec.get("property")),
                ("Reviewed", spec.get("reviewed_at"))]
        if spec.get("business_date"):
            meta.append(("Business date", spec["business_date"]))
        dr = spec.get("date_ranges") or {}
        if dr.get("previous_90"):
            meta.append(("Previous 90 days", dr["previous_90"]))
        if dr.get("next_90"):
            meta.append(("Next 90 days", dr["next_90"]))
        for label, val in meta:
            if val:
                f.append(Paragraph(f"<b>{_esc(label)}:</b> {_esc(val)}", st_prop))
        f.append(Spacer(1, 4))
        if spec.get("disclaimer"):
            f.append(Paragraph(f'<font color="#0B7477">{_esc(spec["disclaimer"])}</font>', st_note))
        if spec.get("completion_warning"):
            f.append(Spacer(1, 3))
            f.append(Paragraph(f'<b><font color="#B00020">INCOMPLETE AUDIT - {_esc(spec["completion_warning"])}</font></b>', st_prop))
        f.append(Spacer(1, 10))

        # ------- sections -------
        for sec in spec.get("sections", []):
            f.append(Paragraph(_esc(sec.get("heading") or ""), st_h2))
            f.append(HRFlowable(width="100%", thickness=0.7, color=TEAL))
            f.append(Spacer(1, 4))
            for para in sec.get("body", []):
                f.append(Paragraph(_esc(para), st_body))
                f.append(Spacer(1, 4))
            for tspec in sec.get("tables", []):
                if tspec.get("table_title"):
                    f.append(Spacer(1, 3))
                    f.append(Paragraph(f"<b>{_esc(tspec['table_title'])}</b>", st_body))
                    f.append(Spacer(1, 2))
                f.append(_table(tspec))
                if tspec.get("summary"):
                    f.append(Spacer(1, 2))
                    f.append(Paragraph(_esc(tspec["summary"]), st_summary))
                f.append(Spacer(1, 8))
            for n in sec.get("notes", []):
                f.append(Paragraph(f"• {_esc(n)}", st_note))
                f.append(Spacer(1, 2))
            f.append(Spacer(1, 4))

        # ------- limitations -------
        if spec.get("limitations"):
            f.append(Paragraph("Notes &amp; Limitations", st_h2))
            f.append(HRFlowable(width="100%", thickness=0.7, color=TEAL))
            f.append(Spacer(1, 4))
            for n in spec["limitations"]:
                f.append(Paragraph(f"• {_esc(n)}", st_note))
                f.append(Spacer(1, 2))
        return f

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(MARGIN, 0.3 * inch, "Read-only audit - generated by Kolo")
        canvas.drawRightString(PAGE_W - MARGIN, 0.4 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=0.5 * inch, bottomMargin=0.55 * inch,
        title=spec.get("title") or "Audit Report", author="Kolo",
    )
    doc.build(build_flowables(), onFirstPage=_footer, onLaterPages=_footer)
    print(f"PDF written to {out_path} (reportlab)")


# ---------------------------------------------------------------------------
# HTML + headless Chromium fallback
# ---------------------------------------------------------------------------
def render_html_fallback(spec, out_path):
    def esc(s):
        return _html.escape(s or "")

    rows_html = []
    for sec in spec.get("sections", []):
        sec_html = [f"<h2>{esc(sec.get('heading'))}</h2>"]
        for para in sec.get("body", []):
            sec_html.append(f"<p>{esc(para)}</p>")
        for t in sec.get("tables", []):
            if t.get("table_title"):
                sec_html.append(f"<div class='tbl-title'>{esc(t['table_title'])}</div>")
            hdrs = "".join(f"<th>{esc(h)}</th>" for h in t.get("headers", []))
            body = ""
            table_rows = t.get("rows", []) or [["None found"]]
            for r in table_rows:
                cells = "".join(
                    f"<td class={'neg' if isinstance(c, float) and c < 0 else ''}>{esc(_cell_text(c))}</td>"
                    for c in r
                )
                body += f"<tr>{cells}</tr>"
            sec_html.append(f"<table><thead><tr>{hdrs}</tr></thead><tbody>{body}</tbody></table>")
            if t.get("summary"):
                sec_html.append(f"<div class='summary'>{esc(t['summary'])}</div>")
        for n in sec.get("notes", []):
            sec_html.append(f"<ul><li>{esc(n)}</li></ul>")
        rows_html.append("".join(sec_html))

    lims = ""
    if spec.get("limitations"):
        lims = "<h2>Notes &amp; Limitations</h2><ul>" + "".join(f"<li>{esc(n)}</li>" for n in spec["limitations"]) + "</ul>"

    meta = "".join(
        f"<div><b>{esc(k)}:</b> {esc(v)}</div>"
        for k, v in [
            ("Property", spec.get("property")),
            ("Reviewed", spec.get("reviewed_at")),
            ("Business date", spec.get("business_date")),
            ("Previous 90 days", (spec.get("date_ranges") or {}).get("previous_90")),
            ("Next 90 days", (spec.get("date_ranges") or {}).get("next_90")),
        ] if v
    )
    warn = f"<div class='warn'>INCOMPLETE AUDIT - {esc(spec.get('completion_warning'))}</div>" if spec.get("completion_warning") else ""

    html_doc = f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: Letter; margin: 0.5in 0.5in 0.6in; }}
body {{ font-family: Helvetica, Arial, sans-serif; color:#222; font-size: 9.5pt; line-height: 1.35; }}
h1 {{ color:#203F68; font-size:20pt; margin:0 0 2pt; }}
h2 {{ color:#0B7477; font-size:13pt; border-bottom:1px solid #0B7477; margin:14pt 0 5pt; }}
p {{ margin:4pt 0; }}
.meta {{ margin:6pt 0; }}
.disclaimer {{ color:#0B7477; font-size:8.5pt; }}
.warn {{ color:#B00020; font-weight:bold; margin:4pt 0; }}
table {{ border-collapse:collapse; width:100%; margin:5pt 0; }}
th {{ background:#203F68; color:#fff; font-size:8.5pt; text-align:left; padding:5pt; }}
td {{ border:0.5pt solid #C8D2DD; padding:4pt 5pt; font-size:8.5pt; }}
tr:nth-child(even) td {{ background:#EEF3F8; }}
.neg {{ color:#B00020; }}
.tbl-title {{ font-weight:bold; margin-top:7pt; }}
.summary {{ font-weight:bold; color:#203F68; margin-top:2pt; }}
ul {{ margin:3pt 0 3pt 14pt; padding:0; font-size:8.5pt; color:#5A6472; }}
.footer {{ position:fixed; left:0; right:0; bottom:-0.35in; color:#5A6472; font-size:7.5pt; }}
.page-number {{ float:right; }}
.page-number:after {{ content:counter(page); }}
</style></head><body>
<div class="footer"><span>Read-only audit - generated by Kolo</span><span class="page-number">Page </span></div>
<h1>{esc(spec.get("title") or "Audit Report")}</h1>
<hr style="border:1.2pt solid #1F3A5F">
<div class="meta">{meta}</div>
<div class="disclaimer">{esc(spec.get("disclaimer") or "")}</div>
{warn}
{''.join(rows_html)}
{lims}
</body></html>"""

    import os
    import subprocess
    import tempfile
    html_path = os.path.splitext(out_path)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    cmd = [
        "chromium", "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={out_path}", html_path,
    ]
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print(f"PDF written to {out_path} (chromium fallback)")
    else:
        print(f"Could not generate PDF. An HTML version was written to {html_path}", file=sys.stderr)
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description="Render a choiceadvantage audit PDF from a JSON spec.")
    ap.add_argument("spec", help="path to JSON spec, or '-' for stdin")
    ap.add_argument("-o", "--out", required=True, help="output PDF path")
    args = ap.parse_args()

    if args.spec == "-":
        raw = sys.stdin.read()
    else:
        with open(args.spec, "r", encoding="utf-8") as fh:
            raw = fh.read()
    spec = json.loads(raw)

    try:
        import reportlab  # noqa: F401
        render_reportlab(spec, args.out)
    except ImportError:
        render_html_fallback(spec, args.out)


if __name__ == "__main__":
    main()
