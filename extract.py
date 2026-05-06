#!/usr/bin/env python3
"""Extract totem-pole data + images from the source Google Sheet into data.csv and images/.

Downloads the sheet as XLSX if not already present (the binary is gitignored — re-run to refresh).
"""
import csv
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SHEET_ID = "18dIGNWnrkRBldfRiPxQqi38WmKZocmh4bTDIcN43C7s"
XLSX_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"

ROOT = Path(__file__).parent
XLSX = ROOT / "totem-poles.xlsx"
OUT_DATA = ROOT / "data.csv"
CSV_COLUMNS = ["name", "location", "plaque", "year", "artists", "affiliation", "notes", "images"]
OUT_IMAGES = ROOT / "images"


def ensure_xlsx():
    if XLSX.exists():
        return
    print(f"Downloading {XLSX_URL} -> {XLSX.name}")
    with urllib.request.urlopen(XLSX_URL) as r, XLSX.open("wb") as f:
        shutil.copyfileobj(r, f)

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# Column positions (0-indexed) for image columns in the drawing XML
IMAGE_COL_LABELS = {2: "front", 3: "back", 4: "plaque"}

# Sheet column letter -> field
SHEET_COLUMNS = {
    "A": "name",
    "B": "location",
    "F": "plaque",
    "G": "year",
    "H": "artists",
    "I": "affiliation",
    "J": "notes",
}


def col_letter(ref):
    return re.match(r"([A-Z]+)", ref).group(1)


def row_number(ref):
    return int(re.search(r"(\d+)", ref).group(1))


def parse_shared_strings(zf):
    """Return list of strings; each <si> may be plain <t> or rich <r><t>...</t>...</r>."""
    with zf.open("xl/sharedStrings.xml") as f:
        tree = ET.parse(f)
    strings = []
    for si in tree.getroot().findall("main:si", NS):
        # Concatenate every <t> descendant in order.
        texts = [t.text or "" for t in si.iter("{%s}t" % NS["main"])]
        strings.append("".join(texts))
    return strings


def parse_sheet_rels(zf):
    """rel id -> target URL (for hyperlinks) or None for non-hyperlink rels."""
    with zf.open("xl/worksheets/_rels/sheet1.xml.rels") as f:
        tree = ET.parse(f)
    rels = {}
    for r in tree.getroot().findall("rel:Relationship", NS):
        rels[r.get("Id")] = r.get("Target")
    return rels


def parse_drawing_rels(zf):
    """rel id -> media filename (e.g. image6.jpg)."""
    with zf.open("xl/drawings/_rels/drawing1.xml.rels") as f:
        tree = ET.parse(f)
    rels = {}
    for r in tree.getroot().findall("rel:Relationship", NS):
        target = r.get("Target")  # e.g. "../media/image6.jpg"
        rels[r.get("Id")] = target.rsplit("/", 1)[-1]
    return rels


def parse_drawings(zf, drawing_rels):
    """Return list of (sheet_row_1indexed, sheet_col_0indexed, image_filename)."""
    with zf.open("xl/drawings/drawing1.xml") as f:
        tree = ET.parse(f)
    out = []
    for anchor in tree.getroot().findall("xdr:oneCellAnchor", NS):
        from_el = anchor.find("xdr:from", NS)
        col = int(from_el.find("xdr:col", NS).text)
        row0 = int(from_el.find("xdr:row", NS).text)
        blip = anchor.find(".//{%s}blip" % "http://schemas.openxmlformats.org/drawingml/2006/main")
        rid = blip.get("{%s}embed" % NS["r"])
        out.append((row0 + 1, col, drawing_rels[rid]))
    return out


def parse_sheet(zf, shared_strings, sheet_rels):
    """Return list of dicts keyed by 1-indexed row number, plus header-row hyperlink lookup."""
    with zf.open("xl/worksheets/sheet1.xml") as f:
        tree = ET.parse(f)

    # Map cell ref -> hyperlink URL
    hyperlinks = {}
    for hl in tree.getroot().findall(".//main:hyperlink", NS):
        ref = hl.get("ref")
        rid = hl.get("{%s}id" % NS["r"])
        if rid in sheet_rels:
            hyperlinks[ref] = sheet_rels[rid]

    rows = {}
    for row in tree.getroot().findall(".//main:row", NS):
        rnum = int(row.get("r"))
        row_data = {"_hyperlinks": {}}
        for c in row.findall("main:c", NS):
            ref = c.get("r")
            letter = col_letter(ref)
            t = c.get("t")
            v = c.find("main:v", NS)
            if v is None:
                continue
            value = v.text
            if t == "s":
                value = shared_strings[int(value)]
            elif t is None:
                # Numeric — keep as string but trim trailing .0
                if value.endswith(".0"):
                    value = value[:-2]
            row_data[letter] = value
            if ref in hyperlinks:
                row_data["_hyperlinks"][letter] = hyperlinks[ref]
        rows[rnum] = row_data
    return rows


def main():
    ensure_xlsx()
    if OUT_IMAGES.exists():
        shutil.rmtree(OUT_IMAGES)
    OUT_IMAGES.mkdir()

    with zipfile.ZipFile(XLSX) as zf:
        shared_strings = parse_shared_strings(zf)
        sheet_rels = parse_sheet_rels(zf)
        drawing_rels = parse_drawing_rels(zf)
        drawings = parse_drawings(zf, drawing_rels)
        rows = parse_sheet(zf, shared_strings, sheet_rels)

        # Group images by sheet row
        images_by_row = {}
        for sheet_row, col, fname in drawings:
            images_by_row.setdefault(sheet_row, []).append((col, fname))

        # Build records — header is row 1, data starts at row 2
        records = []
        data_idx = 0
        for sheet_row in sorted(rows):
            if sheet_row == 1:
                continue
            row = rows[sheet_row]
            if not row.get("A"):
                continue  # skip empty trailing rows

            record = {}
            for letter, field in SHEET_COLUMNS.items():
                val = row.get(letter, "") or ""
                # Prefer hyperlink target for location/notes if cell text is empty or differs
                if letter in row.get("_hyperlinks", {}) and field in ("location",):
                    val = row["_hyperlinks"][letter]
                record[field] = val.strip()

            # Copy images
            imgs = []
            for col, fname in sorted(images_by_row.get(sheet_row, []), key=lambda x: x[0]):
                ext = Path(fname).suffix
                kind = IMAGE_COL_LABELS.get(col, f"col{col}")
                out_name = f"{data_idx:02d}-{len(imgs)+1}-{kind}{ext}"
                with zf.open(f"xl/media/{fname}") as src, (OUT_IMAGES / out_name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                imgs.append(f"images/{out_name}")
            record["images"] = "|".join(imgs)

            records.append(record)
            data_idx += 1

    # Untested: CSV output path has not been re-run end-to-end.
    with OUT_DATA.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
    total_images = sum(r["images"].count("|") + 1 if r["images"] else 0 for r in records)
    print(f"Wrote {len(records)} records to {OUT_DATA.relative_to(ROOT)}")
    print(f"Wrote {total_images} images to {OUT_IMAGES.relative_to(ROOT)}/")


if __name__ == "__main__":
    sys.exit(main())
