# Battery passport extractor

Sends a battery cell datasheet PDF to the Claude API and gets back structured fields for an EU
battery passport: each field has a value, unit, page number and a verbatim quote from that page.

## Run

System `python` is conda and lacks the dependencies. Use the project venv:

```
.venv/bin/python extract.py documents/<name>.pdf              # extract -> outputs/<name>.json
.venv/bin/python extract.py documents/<name>.pdf --verify     # extract, then check quotes
.venv/bin/python extract.py documents/<name>.pdf --verify-only  # re-check saved JSON, no API call
```

Batch mode runs every PDF in `documents/` (sequentially; one bad file never stops the run):

```
.venv/bin/python extract.py --all --verify --limit 5   # at most 5 new API calls this run
.venv/bin/python extract.py --all --verify             # the rest; files with output are reused
.venv/bin/python extract.py --all --verify-only        # free: re-check saved outputs only
.venv/bin/python extract.py --all --force              # re-extract everything (costs tokens)
```

Existing `outputs/<name>.json` is reused (resume-friendly), so `--limit` only counts new API
calls. Each run prints a summary table and writes `outputs/_batch_summary.json` (skip files
starting with `_` when aggregating outputs). Exit code is 1 on any failure or verify failure.
Try `--limit 3` first: each file is roughly 5k-50k input tokens.

Needs `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). `--verify` exits 1 if any quote is not on
its stated page.

## Layout

- `extract.py`: everything lives here: `FIELDS`, `PROMPT`, `validate()`, `verify_quotes()`, `extract()`.
- `documents/`: local datasheet PDFs, **gitignored** (third-party copyright). `documents/SOURCES.md`
  is the tracked manifest of where each came from.
- `outputs/`: generated JSON, gitignored.

## Conventions

- `FIELDS` is the single source of truth for the field list. The prompt is built from it.
- Numeric fields hold plain numbers; tolerances and caveats go in the optional `note` key.
- Never calculate or guess: if the document doesn't state a value, it is `null` with a note
  (e.g. `energy_wh` is not voltage x capacity; current limits given only as a C-rate are null).
- Quotes must be verbatim and appear on the named page. `verify_quotes()` checks this (ignores
  whitespace, case, `℃` vs `°C`; `...` splits a quote into parts that must all appear).
- Dimensions: prismatic/pouch use `width_mm`/`thickness_mm`/`height_mm`, cylindrical uses
  `diameter_mm`/`height_mm`; non-applicable fields are null.
- `cycle_life` is a list, one object per test condition (see `CycleLifeTest`).

## Gotchas

- The response can start with a thinking block; take the first block with `type == "text"`, not
  `content[0]`.
- `validate()` only warns on shape problems (it doesn't abort), so one bad field doesn't waste an
  API call. Quote verification is a separate opt-in step.
- Page numbers mean position in the PDF file (first page = 1), not printed page labels; the prompt
  says so because the model was off by one on CALB_CA100 before.
- Some PDFs' text layers leak glyph names (`/Oslash.sc` for Ø, `Speci/f_ications`); `_norm()`
  maps these. If `--verify` fails on a quote that is plainly on the page, check the raw
  `pypdf` text first; it is often an extraction artefact, not a model error.
- Output wording varies between runs (e.g. quotes, notes). Run `--verify` on every extraction.
- `--verify` checks quote placement, not truth. Values still need a human spot-check.
- The Battery Pass longlist is not in the repo. The field list was assembled from the Battery Pass
  data model and Regulation (EU) 2023/1542 Annex XIII, keeping only what datasheets plausibly contain.

## When adding or changing a field

1. Edit `FIELDS`; add it to `NUMERIC_FIELDS` (or add a check in `validate()`) if structured.
2. Re-run `--verify` on one prismatic, one cylindrical and one sodium-ion datasheet
   (e.g. `EVE_LF280N`, `LG_INR18650MH1`, `HKD_Na18650-1300`).
