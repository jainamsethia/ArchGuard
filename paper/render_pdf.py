"""Convert the built DOCX to PDF with Word, then rasterise every page.

Word rather than LibreOffice because the template's numbering is field driven:
the heading, table and reference numbers are produced by the template's own list
definitions, and only Word resolves them the way a reviewer opening the DOCX
will see. The page count it reports is therefore the authoritative one for the
venue's page limit.

Requires Windows with Word installed (pywin32). The rasterised pages are a
convenience for eyeballing the layout and are not part of the deliverable.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCX = ROOT / "ArchGuard_IEEE_paper.docx"
PDF = DOCX.with_suffix(".pdf")
IMG_DIR = ROOT / "build/pages"

WD_FORMAT_PDF = 17
WD_STATISTIC_PAGES = 2


def to_pdf() -> None:
    import win32com.client as win32
    if PDF.exists():
        PDF.unlink()
    word = win32.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        doc = word.Documents.Open(str(DOCX), ReadOnly=False, AddToRecentFiles=False)
        try:
            # repaginate so field-driven numbering is resolved before export
            doc.Fields.Update()
            doc.Repaginate()
            print(f"pages reported by Word: {doc.ComputeStatistics(WD_STATISTIC_PAGES)}")
            doc.SaveAs(str(PDF), FileFormat=WD_FORMAT_PDF)
        finally:
            doc.Close(SaveChanges=0)
    finally:
        word.Quit()


def rasterise() -> None:
    import fitz
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for old in IMG_DIR.glob("*.jpg"):
        old.unlink()
    doc = fitz.open(str(PDF))
    print(f"pdf pages: {doc.page_count}")
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(dpi=110)
        out = IMG_DIR / f"page-{i:02d}.jpg"
        pix.save(str(out))
        print(f"  {out.name}  {pix.width}x{pix.height}")
    doc.close()


if __name__ == "__main__":
    to_pdf()
    rasterise()
    sys.exit(0)
