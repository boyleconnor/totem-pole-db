# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static, single-page catalog of totem poles in the Seattle area. Two pieces:

- `extract.py` — pulls the source Google Sheet (XLSX export) and writes `data.csv` + `images/`.
- `index.html` — self-contained page (vanilla JS, inline CSS) that `fetch()`es `data.csv` and renders cards.

There is no build step, no package manager, no test suite.

## Running

Refresh data from the source sheet:

```
python3 extract.py
```

This downloads `totem-poles.xlsx` (gitignored) on first run; delete it to force a re-download. Stdlib only — no pip install needed.

Serve the site locally (the page uses `fetch('data.csv')`, which browsers block under `file://`):

```
python3 -m http.server
```

Then open `http://localhost:8000/`.

## Data pipeline (extract.py)

The script unpacks the XLSX as a zip and reads the raw XML rather than using a third-party library. Key joins to be aware of when changing column mappings:

- `SHEET_COLUMNS` (A,B,F,G,H,I,J → field names) maps spreadsheet columns to CSV fields. Column letters are tied to the sheet layout — if the sheet's columns shift, update this dict.
- `IMAGE_COL_LABELS` (cols 2,3,4 → front/back/plaque) maps the *0-indexed drawing-anchor column* to a label suffix. Images are matched to rows via `xl/drawings/drawing1.xml` anchors, not by being in a cell.
- Output image filenames encode position: `{record_idx:02d}-{n}-{kind}.{ext}`. The frontend (`imageKindFromPath` in `index.html`) parses `-(front|back|plaque)\.` out of these names to label thumbnails — keep the suffix intact if you rename.
- `location` cell text is overridden by the cell's hyperlink target when present (the sheet stores Google Maps URLs as hyperlinks on plain-text cells).
- Each run wipes and recreates `images/`.

The header comment in `extract.py` notes: *"CSV output path has not been re-run end-to-end."* Treat the CSV-writing portion as lightly tested.

## Frontend notes (index.html)

- Hand-rolled CSV parser (handles quoted fields, doubled quotes, CRLF). Don't swap in a library — the file is intentionally dependency-free.
- Card fields are rendered through `escapeHtml`; the only raw-HTML interpolation is the badge/anchor markup the script itself constructs. Preserve that pattern when adding fields.
- URLs inside `notes` and `plaque` are extracted with a regex and surfaced as a "References" list; the matching URLs are then stripped from the displayed body text.
- Affiliation badges get a `non-indigenous` modifier class when the trimmed affiliation string contains "non-indigenous" (case-insensitive).
