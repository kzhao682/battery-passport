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

Batch mode runs every PDF in `documents/` (sequentially; one bad file never stops the run).
`--devset` does the same for only the 10 files in `evals/devset.json` (the Day 2 dev set;
`label_day3` marks the 5 to hand-label). `make devset` / `make devset-verify` wrap this.

```
.venv/bin/python extract.py --devset --verify          # just the dev set
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

- `schema.py`: **the single source of truth for the fields**: nested Pydantic models
  (`DatasheetExtraction` > `GeneralInfo`, `Materials`, `Performance`, `Durability`,
  `OperatingConditions`), each field an `Evidence` (or list of them). `python schema.py` prints the
  JSON Schema. `NUMERIC_PATHS` lists fields whose value must be a number or range, not text.
- `extract.py`: the prompt (built from schema.py's descriptions), `extract()`, `validate()`,
  `verify_quotes()`, batch mode.
- `docs/blueprint.md`: the 14-day project plan (local only, gitignored, so it may be absent in a
  fresh clone). `docs/field_map.md`: how each schema field maps to the Battery Pass data model.
- `evals/devset.json`: the 10 dev datasheets. Gold labels (Day 3) go in `evals/labels/<name>.json`.
- `documents/`: local datasheet PDFs, **gitignored** (third-party copyright). `documents/SOURCES.md`
  is the tracked manifest of where each came from.
- `outputs/`: generated JSON, gitignored. `outputs/v0_flat/` holds the older flat-schema outputs.

## Conventions

- Add or change fields in `schema.py` only; the prompt and validation follow automatically.
- Each field is an evidence object `{value, unit, conditions, page, quote, confidence, note}`;
  `document_id` is stamped by `extract()`, not by the model. Single-valued fields are an object or
  null; fields that depend on test conditions (`rated_capacity`, `cycle_life`, currents,
  `internal_resistance`, `storage_temperature`) are lists, one entry per condition.
- Values are plain numbers with the unit **as stated** (`5220` + `g`); no conversion at extraction
  time (normalization is a separate Day 6 step). Ranges are `{min, max}`; tolerances go in `note`.
- Never calculate or guess: if the document doesn't state a value, it is `null` (or `[]`). e.g.
  `rated_energy` is not voltage x capacity.
- Quotes must be verbatim and appear on the named page. `verify_quotes()` checks this (ignores
  whitespace, case, `℃` vs `°C`; `...` splits a quote into parts that must all appear).
- Dimensions: an object with any of length/width/thickness/height/diameter; cylindrical cells use
  diameter + height.

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
- The BatteryPass-Ready longlist v2.0 (Excel) is not in the repo. `docs/field_map.md` was built from
  the Battery Pass data model v1.2.0 and should be re-checked against the longlist before Day 11.
- A `--verify` failure on a quote the model read from an image or drawing is "unverifiable", not wrong:
  the value is not in the PDF's text layer at all (seen on CATL dimension drawings and
  SONY_US18650VTC6_techinfo). Check those by eye.

## When adding or changing a field

1. Edit `schema.py` (add the field with a clear `description`; add its path to `NUMERIC_PATHS` if the
   value must be numeric).
2. Re-run `make devset` (or `--force` on a few files) and check the 10 dev-set datasheets still
   validate; these cover prismatic, cylindrical, sodium-ion, bilingual ZH+EN and image-heavy layouts.
3. If gold labels exist (Day 3+), update `evals/labels/` and `docs/field_map.md` too.
