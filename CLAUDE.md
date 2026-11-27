# Battery passport extractor

Sends a battery cell datasheet PDF to the Claude API and gets back structured fields for an EU
battery passport: each field has a value, unit, page number and a verbatim quote from that page.

## Ground rules

- Never run an extraction or call the Anthropic API without asking Kevin first; reuse the saved
  files in `outputs/` instead. That covers `python -m battery_passport` without `--verify-only`,
  `--all`/`--devset`/`--gold` runs that need new files, `--force`, and `make devset` / `make batch` /
  `make v1` / `make v2`.
  `--verify-only`, `make devset-verify` and `make test` are free and fine to run.

## Run

Reminder: commands below that call the API need Kevin's OK first (see Ground rules).

System `python` is conda and lacks the dependencies. Use the project venv:

```
.venv/bin/python -m battery_passport documents/<name>.pdf              # extract -> outputs/<name>.json
.venv/bin/python -m battery_passport documents/<name>.pdf --verify     # extract, then check quotes
.venv/bin/python -m battery_passport documents/<name>.pdf --verify-only  # re-check saved JSON, no API call
```

Batch mode runs every PDF in `documents/` (sequentially; one bad file never stops the run).
`--devset` does the same for only the 10 files in `evals/devset.json` (the Day 2 dev set;
`label_day3` marks the 5 to hand-label). `make devset` / `make devset-verify` wrap this.

```
.venv/bin/python -m battery_passport --devset --verify          # just the dev set
.venv/bin/python -m battery_passport --all --verify --limit 5   # at most 5 new API calls this run
.venv/bin/python -m battery_passport --all --verify             # the rest; files with output are reused
.venv/bin/python -m battery_passport --all --verify-only        # free: re-check saved outputs only
.venv/bin/python -m battery_passport --all --force              # re-extract everything (costs tokens)
```

Existing `outputs/<name>.json` is reused (resume-friendly), so `--limit` only counts new API
calls. Each run prints a summary table and writes `outputs/_batch_summary.json` (skip files
starting with `_` when aggregating outputs). Exit code is 1 on any failure or verify failure.
Try `--limit 3` first: each file is roughly 5k-50k input tokens.

Needs `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). `--verify` exits 1 if any quote is not on
its stated page.

## Tests

`make test` (pytest, ~40 tests, under a second) checks the code, not the model's accuracy; accuracy is
the eval suite (Day 3+). Tests are free: `tests/conftest.py` replaces `anthropic.Anthropic` with a
function that raises, so any accidental real API call fails the test instead of spending credits.
No test uses a real PDF or the network: quote checking runs on plain strings (`check_quotes`), and
`tests/test_cli.py` mocks `extract`. Keep it that way: never add a test that calls the API, and when
you fix a bug, add a small regression test (see `test_glyph_name_oslash_matches_diameter_sign`).
Install test deps with `pip install -r requirements-dev.txt`.

## Layout

All Python lives in the `battery_passport/` package. Run everything **from the repo root**
(`documents/`, `outputs/` and `evals/` are relative paths). New code goes in a new module in
the package, one job per module; don't add `.py` files at the repo root.

- `battery_passport/schema.py`: **the single source of truth for the fields**: nested Pydantic
  models (`DatasheetExtraction` > `GeneralInfo`, `Materials`, `Performance`, `Durability`,
  `OperatingConditions`), each field an `Evidence` (or list of them).
  `python -m battery_passport.schema` prints the JSON Schema. `NUMERIC_PATHS` lists fields whose
  value must be a number or range, not text.
- `battery_passport/extract.py`: the prompt (built from schema.py's descriptions) and the Claude
  call, `extract()`.
- `battery_passport/validate.py`: plain-Python checks, no AI: `validate()` (shape against the
  schema) and `verify_quotes()` (each quote appears on its stated page). Normalization and
  plausibility rules (Day 6+) belong here or in a sibling module.
- `battery_passport/cli.py`: argument parsing, `process_pdf()`, `run_batch()` and the summary.
  `__main__.py` makes `python -m battery_passport` work.
- `battery_passport/extract_v1.py`: Day 4 plain-prompt baseline (`--gold --v1` -> `outputs/v1/`).
- `battery_passport/extract_v2.py`: Day 5 structured outputs (`--gold --v2` -> `outputs/v2/`), one
  call per field group. The API rejects the whole schema at once, and rejects a group whose keys
  are optional, so `api_group_model()` derives an all-required, nullable schema from `schema.py`.
  No prompt caching: it was written on every call and never read. `outputs/*.json` (top level) is
  still the older single-call extractor in `extract.py`.
- `battery_passport/labels.py`: writes blank gold-label templates (`make labels`, never overwrites).
  `battery_passport/evaluate.py`: scores `outputs/` against `evals/labels/` (`make eval`, free);
  results go to `evals/results/` (tracked; `make eval`, `eval-v1`, `eval-v2`). Descriptive text
  fields match loosely (`LOOSE_TEXT_PATHS`); `general.model`, numbers and units do not, except
  that the ohm symbol equals "Ohm". `general.battery_type` only needs the same form factor and no
  module/pack conflict.
- `docs/blueprint.md`: the 14-day project plan (local only, gitignored, so it may be absent in a
  fresh clone). `docs/field_map.md`: how each schema field maps to the Battery Pass data model.
- `evals/devset.json`: the 10 dev datasheets. Gold labels (Day 3) are in `evals/labels/<name>.json`
  (5 files, ~100 values, hand-labelled from the PDFs). Edit labels by hand; **never hand-edit
  `outputs/`** (the eval compares the two; re-extract instead). A label `value: null` means "not
  stated". Some labels deliberately infer (LG chemistry "NMC" from "INR", WINSTON max 65 degC), so
  the model returning null there counts as a miss. That is expected until the prompt changes.
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
