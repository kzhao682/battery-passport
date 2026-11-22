"""Day 1: send one battery datasheet PDF to Claude and get JSON back.

Usage:
    python extract.py path/to/datasheet.pdf                 # extract
    python extract.py path/to/datasheet.pdf --verify        # extract, then check quotes
    python extract.py path/to/datasheet.pdf --verify-only   # re-check saved JSON, no API call

Writes the result to outputs/<datasheet-name>.json and prints it.
"""

import argparse
import base64
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

load_dotenv()  # reads ANTHROPIC_API_KEY from .env
logging.getLogger("pypdf").setLevel(logging.ERROR)  # silence per-font encoding warnings

MODEL = "claude-sonnet-5-5"

# Passport fields that cell datasheets can plausibly contain, drawn from the Battery Pass data
# model and EU Battery Regulation 2023/1542 (Annex XIII). Attributes datasheets almost never
# cover (carbon footprint, recycled content, due diligence) are left out on purpose.
# Dimensions cover all formats: prismatic/pouch use width/thickness/height, cylindrical uses
# diameter/height; fields that don't apply to a cell should come back null.
FIELDS = {
    # --- identity ---
    "manufacturer": "Company that makes the battery or cell",
    "model": "Model or part number",
    "chemistry": "Cell chemistry, e.g. LFP, NMC, NCA, LCO, sodium-ion",
    "cell_format": "Cell format: one of cylindrical, prismatic, pouch (or null if not stated)",
    # --- electrical ---
    "nominal_voltage_v": "Nominal (typical) voltage in volts",
    "max_charge_voltage_v": "Maximum / charge cut-off voltage in volts",
    "discharge_cutoff_voltage_v": "Minimum / discharge cut-off voltage in volts",
    "rated_capacity_ah": "Rated (nominal or minimum) capacity in amp-hours",
    "energy_wh": "Rated energy in watt-hours (only if stated; do not multiply voltage by capacity)",
    "internal_resistance_mohm": "Internal resistance (AC or DC) in milliohms",
    "max_charge_current_a": (
        "Maximum continuous charge current in amps. If only a C-rate is given, return null and "
        "put the C-rate in the note"
    ),
    "max_discharge_current_a": (
        "Maximum continuous discharge current in amps. If only a C-rate is given, return null "
        "and put the C-rate in the note"
    ),
    # --- physical ---
    "weight_kg": "Weight in kilograms",
    "diameter_mm": "Cell diameter in millimetres (cylindrical cells only; nominal number, no tolerance)",
    "width_mm": "Cell width in millimetres (prismatic/pouch; nominal number only, no tolerance)",
    "thickness_mm": "Cell thickness in millimetres (prismatic/pouch; nominal number only, no tolerance)",
    "height_mm": (
        "Cell total height (cylindrical: length) in millimetres, including terminals "
        "(nominal number only)"
    ),
    # --- lifetime ---
    "cycle_life": (
        "List of rated cycle-life results, one object per test condition, each with keys: "
        "cycles (number), temperature_c (number or null), charge_rate_c (number or null), "
        "discharge_rate_c (number or null), end_of_life_capacity_pct (number or null, "
        "remaining capacity at end of life), conditions (other test conditions as short text, or null)"
    ),
    "charge_temp_min_c": "Minimum charging temperature in degrees Celsius",
    "charge_temp_max_c": "Maximum charging temperature in degrees Celsius",
    "discharge_temp_min_c": "Minimum discharging temperature in degrees Celsius",
    "discharge_temp_max_c": "Maximum discharging temperature in degrees Celsius",
    "storage_temp_min_c": "Minimum storage temperature in degrees Celsius",
    "storage_temp_max_c": "Maximum storage temperature in degrees Celsius",
    # --- compliance ---
    "certifications": (
        "List of safety/transport/quality standards or certifications the document says the "
        "cell meets or was tested to (e.g. UN38.3, IEC 62619, UL 1642), as a list of strings"
    ),
}

PROMPT = f"""You are extracting data for an EU battery passport from a battery datasheet.

For each field below, return the value from the document, the page it is on, and a short
exact quote from the page that contains it. If the document does not contain a field,
use null for the value and say so. Do not guess or calculate values that are not stated.

Quotes must be verbatim: copy the text exactly as it appears on that page, as one
contiguous passage. Do not paraphrase, reorder, merge text from different table cells or lines,
or add words that are not there. If the value and its label are in separate cells, quote only
the cell that holds the value, or use "..." between two short exact fragments. A shorter exact
quote is better than a longer inexact one.

Page numbers: "page" is the position of the page in the PDF file, counting the first page of
the file as 1. Ignore any page numbers printed on the pages themselves (cover pages, roman
numerals and "Page X of Y" footers often do not match the file position).

Fields:
{json.dumps(FIELDS, indent=2)}

Reply with JSON only, no other text, in this shape:
{{
  "<field_name>": {{"value": ..., "unit": "...", "page": 1, "quote": "..."}},
  ...
}}

Values must be plain numbers where the field description says number (no units or tolerances
inside the value; put tolerances in an optional "note" key). The cycle_life value is a JSON
list of objects as described, with the unit set to "cycles"."""


class CycleLifeTest(BaseModel):
    cycles: float
    temperature_c: float | None = None
    charge_rate_c: float | None = None
    discharge_rate_c: float | None = None
    end_of_life_capacity_pct: float | None = None
    conditions: str | None = None


NUMERIC_FIELDS = [
    "nominal_voltage_v", "max_charge_voltage_v", "discharge_cutoff_voltage_v",
    "rated_capacity_ah", "energy_wh", "internal_resistance_mohm",
    "max_charge_current_a", "max_discharge_current_a",
    "weight_kg", "diameter_mm", "width_mm", "thickness_mm", "height_mm",
    "charge_temp_min_c", "charge_temp_max_c",
    "discharge_temp_min_c", "discharge_temp_max_c",
    "storage_temp_min_c", "storage_temp_max_c",
]
CELL_FORMATS = {"cylindrical", "prismatic", "pouch"}


def validate(result: dict) -> list[str]:
    """Return a list of problems with the structured fields (empty if all good)."""
    problems = [f"missing field: {k}" for k in FIELDS if k not in result]

    for key in NUMERIC_FIELDS:
        value = result.get(key, {}).get("value")
        # bool is a subclass of int, so exclude it explicitly
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            problems.append(f"{key}: expected a number, got {value!r}")

    fmt = result.get("cell_format", {}).get("value")
    if fmt is not None and fmt not in CELL_FORMATS:
        problems.append(f"cell_format: expected one of {sorted(CELL_FORMATS)}, got {fmt!r}")

    certs = result.get("certifications", {}).get("value")
    if certs is not None and not (isinstance(certs, list) and all(isinstance(c, str) for c in certs)):
        problems.append(f"certifications: expected a list of strings, got {certs!r}")

    cycle_value = result.get("cycle_life", {}).get("value")
    if cycle_value is not None:
        if not isinstance(cycle_value, list):
            problems.append(f"cycle_life: expected a list, got {type(cycle_value).__name__}")
        else:
            for i, item in enumerate(cycle_value):
                try:
                    CycleLifeTest.model_validate(item)
                except ValidationError as e:
                    problems.append(f"cycle_life[{i}]: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
    return problems


class ExtractionError(Exception):
    """The model response could not be turned into a result (e.g. truncated)."""


def extract(pdf_path: Path) -> tuple[dict, dict]:
    """Send one PDF to Claude. Returns (result, {"input_tokens": n, "output_tokens": n})."""
    pdf_data = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )

    if response.stop_reason == "max_tokens":
        raise ExtractionError("response was cut off at max_tokens; raise max_tokens in extract()")

    # The response may begin with a thinking block; take the text block.
    text = next(b.text for b in response.content if b.type == "text").strip()
    # Claude sometimes wraps JSON in ``` fences; strip them if present.
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]

    usage = {"input_tokens": response.usage.input_tokens,
             "output_tokens": response.usage.output_tokens}
    try:
        return json.loads(text), usage
    except json.JSONDecodeError as e:
        raise ExtractionError(f"response was not valid JSON: {e}") from e


def _norm(s: str) -> str:
    """Normalise text for quote matching: ignore whitespace, case and unicode variants."""
    s = unicodedata.normalize("NFKC", s).replace("℃", "°C").lower()  # ℃ -> °C
    # Some PDFs' text layers leak raw glyph names: "/Oslash.sc" for Ø, "Speci/f_ications" for fi.
    s = re.sub(r"/oslash(?:\.[a-z]+)?", "ø", s)
    s = re.sub(r"/((?:f|i|l|t)(?:_(?:f|i|l|t))+)", lambda m: m.group(1).replace("_", ""), s)
    return re.sub(r"\s+", "", s)


def verify_quotes(result: dict, pdf_path: Path) -> list[str]:
    """Check each non-null field's quote appears on the page it names.

    Quotes with '...' are split and every part must appear on that page.
    Returns a list of problems (empty if all quotes check out).
    """
    pages = [_norm(p.extract_text() or "") for p in PdfReader(pdf_path).pages]
    problems = []
    for key, field in result.items():
        quote, page = field.get("quote"), field.get("page")
        if field.get("value") in (None, []) and quote is None:
            continue  # stated as absent (null or empty list): nothing to verify
        if quote is None or not isinstance(page, int):
            problems.append(f"{key}: has a value but no quote/page to check")
            continue
        if not 1 <= page <= len(pages):
            problems.append(f"{key}: page {page} is outside the document (1-{len(pages)})")
            continue
        parts = [_norm(p) for p in re.split(r"\.\.\.|…", quote) if p.strip()]
        if all(p in pages[page - 1] for p in parts):
            continue
        found = [i + 1 for i, t in enumerate(pages) if all(p in t for p in parts)]
        where = f"found on page {found}" if found else "not found anywhere in the PDF"
        problems.append(f"{key}: quote not on page {page} ({where}): {quote!r}")
    return problems


def process_pdf(pdf_path: Path, out_dir: Path, *, verify: bool, verify_only: bool, force: bool) -> dict:
    """Extract (or reuse saved output for) one PDF and return a summary record. Never raises.

    status: "extracted" (API call made), "reused" (saved output kept), "missing" (--verify-only
    but no saved output) or "failed".
    """
    rec = {"file": pdf_path.name, "status": "extracted", "error": None, "warnings": [],
           "verify_failures": [], "quotes_checked": None, "input_tokens": 0, "output_tokens": 0}
    out_path = out_dir / f"{pdf_path.stem}.json"
    try:
        if verify_only or (out_path.exists() and not force):
            if not out_path.exists():
                rec["status"] = "missing"
                return rec
            result = json.loads(out_path.read_text())
            rec["status"] = "reused"
        else:
            result, usage = extract(pdf_path)
            rec.update(usage)
            out_dir.mkdir(exist_ok=True)
            out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        rec["warnings"] = validate(result)
        if verify or verify_only:
            rec["verify_failures"] = verify_quotes(result, pdf_path)
            rec["quotes_checked"] = sum(1 for f in result.values() if f.get("quote") is not None)
    except Exception as e:  # one bad file must not stop the batch
        rec["status"] = "failed"
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def run_batch(args: argparse.Namespace) -> int:
    """Process every PDF in args.dir. Returns the process exit code (1 if anything failed)."""
    pdfs = sorted(args.dir.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {args.dir}")
    out_dir = Path("outputs")
    records, api_calls = [], 0
    print(f"Batch: {len(pdfs)} PDFs in {args.dir}", file=sys.stderr)

    try:
        for i, pdf in enumerate(pdfs, 1):
            out_exists = (out_dir / f"{pdf.stem}.json").exists()
            will_call_api = not args.verify_only and (args.force or not out_exists)
            if will_call_api and args.limit is not None and api_calls >= args.limit:
                continue  # --limit reached: leave the rest for a later run
            rec = process_pdf(pdf, out_dir, verify=args.verify, verify_only=args.verify_only,
                              force=args.force)
            api_calls += rec["status"] in ("extracted", "failed") and will_call_api
            records.append(rec)
            flags = (f"{len(rec['warnings'])} warn, {len(rec['verify_failures'])} verify-fail"
                     if rec["status"] != "failed" else rec["error"])
            print(f"[{i}/{len(pdfs)}] {pdf.name}: {rec['status']} ({flags})", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted: printing summary of what finished.", file=sys.stderr)

    # --- summary ---
    width = max((len(r["file"]) for r in records), default=4)
    print(f"\n{'file':<{width}}  {'status':<9}  warn  vfail  tokens in/out")
    for r in records:
        tokens = f"{r['input_tokens']}/{r['output_tokens']}" if r["input_tokens"] else "-"
        vfail = len(r["verify_failures"]) if r["quotes_checked"] is not None else "-"
        print(f"{r['file']:<{width}}  {r['status']:<9}  {len(r['warnings']):>4}  {vfail!s:>5}  {tokens}")

    count = lambda s: sum(1 for r in records if r["status"] == s)
    n_warn = sum(1 for r in records if r["warnings"])
    n_vfail = sum(1 for r in records if r["verify_failures"])
    tin = sum(r["input_tokens"] for r in records)
    tout = sum(r["output_tokens"] for r in records)
    print(f"\n{len(records)}/{len(pdfs)} processed: {count('extracted')} extracted, "
          f"{count('reused')} reused, {count('missing')} missing output, {count('failed')} failed")
    print(f"{n_warn} with validation warnings, {n_vfail} with quote-verification failures")
    print(f"Tokens used this run: {tin:,} in, {tout:,} out")
    if len(records) < len(pdfs):
        print(f"{len(pdfs) - len(records)} not processed (--limit or interrupt); re-run to continue")
    for r in records:
        if r["status"] == "failed":
            print(f"FAILED {r['file']}: {r['error']}")

    out_dir.mkdir(exist_ok=True)
    (out_dir / "_batch_summary.json").write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print(f"Details in {out_dir / '_batch_summary.json'}", file=sys.stderr)
    return 1 if count("failed") or n_vfail else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract battery passport fields from datasheet PDFs.")
    parser.add_argument("pdf", type=Path, nargs="?", help="path to the datasheet PDF (omit with --all)")
    parser.add_argument("--verify", action="store_true",
                        help="after extracting, check each quote appears on its stated page")
    parser.add_argument("--verify-only", action="store_true",
                        help="skip the API call; verify the existing outputs/<name>.json against the PDF")
    parser.add_argument("--all", action="store_true",
                        help="batch mode: process every PDF in --dir, skipping ones that already have output")
    parser.add_argument("--dir", type=Path, default=Path("documents"),
                        help="folder of PDFs for --all (default: documents)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="with --all: make at most N new API calls this run")
    parser.add_argument("--force", action="store_true",
                        help="with --all: re-extract even if outputs/<name>.json exists")
    args = parser.parse_args()

    if args.all == (args.pdf is not None):
        parser.error("give either a PDF path or --all (not both, not neither)")
    if not args.all and (args.limit is not None or args.force):
        parser.error("--limit and --force only apply with --all")
    if args.all:
        sys.exit(run_batch(args))

    pdf_path = args.pdf
    if not pdf_path.exists():
        sys.exit(f"File not found: {pdf_path}")

    out_dir = Path("outputs")
    out_path = out_dir / f"{pdf_path.stem}.json"

    if args.verify_only:
        if not out_path.exists():
            sys.exit(f"{out_path} not found; run without --verify-only first")
        result = json.loads(out_path.read_text())
    else:
        try:
            result, usage = extract(pdf_path)
        except ExtractionError as e:
            sys.exit(f"Extraction failed: {e}")
        print(f"Tokens: {usage['input_tokens']} in, {usage['output_tokens']} out", file=sys.stderr)
        for problem in validate(result):
            print(f"WARNING: {problem}", file=sys.stderr)

        out_dir.mkdir(exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\nSaved to {out_path}", file=sys.stderr)

    if args.verify or args.verify_only:
        problems = verify_quotes(result, pdf_path)
        for problem in problems:
            print(f"VERIFY FAILED: {problem}", file=sys.stderr)
        checked = sum(1 for f in result.values() if f.get("quote") is not None)
        print(f"Verified {checked - len(problems)}/{checked} quotes against {pdf_path}", file=sys.stderr)
        if problems:
            sys.exit(1)


if __name__ == "__main__":
    main()
