"""Send battery datasheet PDFs to Claude and get structured passport fields back.

The fields, their descriptions and their shapes all come from schema.py (single source of truth).

Usage:
    python extract.py path/to/datasheet.pdf                 # extract
    python extract.py path/to/datasheet.pdf --verify        # extract, then check quotes
    python extract.py path/to/datasheet.pdf --verify-only   # re-check saved JSON, no API call
    python extract.py --all [--verify] [--limit N]          # every PDF in documents/
    python extract.py --devset [--verify] [--limit N]       # only the files in evals/devset.json

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
from typing import get_origin

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError
from pypdf import PdfReader

from schema import NUMERIC_PATHS, DatasheetExtraction, Evidence

load_dotenv()  # reads ANTHROPIC_API_KEY from .env
logging.getLogger("pypdf").setLevel(logging.ERROR)  # silence per-font encoding warnings

MODEL = "claude-sonnet-5-5"
DEVSET_PATH = Path("evals/devset.json")


def _field_listing() -> str:
    """Render the schema's groups and fields (with descriptions) as a compact list for the prompt."""
    lines = []
    for group, group_info in DatasheetExtraction.model_fields.items():
        lines.append(f'"{group}":')
        for name, info in group_info.annotation.model_fields.items():
            shape = "list of evidence objects" if get_origin(info.annotation) is list else "one evidence object"
            lines.append(f"  - {name} ({shape}): {info.description}")
    return "\n".join(lines)


def _evidence_listing() -> str:
    """Describe the keys of one evidence object, from Evidence's own field descriptions."""
    return "\n".join(
        f"  - {name}: {info.description}"
        for name, info in Evidence.model_fields.items()
        if name != "document_id"  # filled in by this script, not by the model
    )


PROMPT = f"""You are extracting data for an EU battery passport from a battery datasheet.

The output has these groups and fields. A field marked "one evidence object" is an object, or
null if the document does not state it. A field marked "list of evidence objects" is a list with
one object per set of conditions, or [] if the document does not state it.

{_field_listing()}

Each evidence object has these keys:
{_evidence_listing()}

Value rules:
- Do not guess or calculate anything that is not stated. If a value is not in the document, use
  null (or [] for lists). Never fill a value from general knowledge.
- For numeric values give a plain number in "value" and the unit as stated in "unit"
  (e.g. value 5220, unit "g"; do not convert). Tolerances go in "note", not in "value".
- A range such as "0.1~0.6" or "-20 to 55" is a {{"min": ..., "max": ...}} object in "value".
- Dimensions are an object with any of length, width, thickness, height, diameter (numbers, in
  the unit stated). Cylindrical cells use diameter and height.
- Put test or use conditions (temperature, C-rate, end-of-life capacity) in "conditions".

Quotes must be verbatim: copy the text exactly as it appears on that page, as one
contiguous passage. Do not paraphrase, reorder, merge text from different table cells or lines,
or add words that are not there. If the value and its label are in separate cells, quote only
the cell that holds the value, or use "..." between two short exact fragments. A shorter exact
quote is better than a longer inexact one.

Page numbers: "page" is the position of the page in the PDF file, counting the first page of
the file as 1. Ignore any page numbers printed on the pages themselves (cover pages, roman
numerals and "Page X of Y" footers often do not match the file position).

Reply with JSON only, no other text, in this shape (all groups present):
{{
  "general": {{"manufacturer": {{"value": "...", "unit": null, "page": 1, "quote": "...", "confidence": "high"}}, "model": null, ...}},
  "materials": {{...}},
  "performance": {{"rated_capacity": [{{"value": 280, "unit": "Ah", "conditions": "0.5C", "page": 6, "quote": "..."}}], ...}},
  "durability": {{...}},
  "operating_conditions": {{...}}
}}"""


def _iter_raw_evidence(result: dict):
    """Yield (path, evidence_dict) for every evidence object in a raw result, tolerating bad shapes.

    Works on the raw JSON rather than the parsed model so one malformed field never hides the rest.
    Paths look like "performance.nominal_voltage" or "durability.cycle_life[1]".
    """
    for group, fields in result.items():
        if not isinstance(fields, dict):
            continue
        for name, item in fields.items():
            if isinstance(item, dict):
                yield f"{group}.{name}", item
            elif isinstance(item, list):
                for i, ev in enumerate(item):
                    if isinstance(ev, dict):
                        yield f"{group}.{name}[{i}]", ev


def validate(result: dict) -> list[str]:
    """Return a list of problems with the result's shape (empty if all good)."""
    try:
        parsed = DatasheetExtraction.model_validate(result)
    except ValidationError as e:
        # A Union field reports one error per member type; keep the first per field location.
        seen, problems = set(), []
        for err in e.errors():
            loc = tuple(err["loc"])
            key = loc[: loc.index("value") + 1] if "value" in loc else loc[:4]
            if key not in seen:
                seen.add(key)
                shown = loc[: loc.index("value") + 1] if "value" in loc else loc
                problems.append(f"{'.'.join(str(p) for p in shown)}: {err['msg']}")
        return problems

    problems = []
    for group, group_value in parsed:
        for name, item in group_value:
            path = f"{group}.{name}"
            evidences = item if isinstance(item, list) else [item]
            for i, ev in enumerate(evidences):
                if ev is None:
                    continue
                where = f"{path}[{i}]" if isinstance(item, list) else path
                if path in NUMERIC_PATHS and isinstance(ev.value, str):
                    problems.append(f"{where}: expected a number or range, got {ev.value!r}")
                if ev.value is not None and (ev.quote is None or ev.page is None):
                    problems.append(f"{where}: has a value but no page/quote")
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
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"response was not valid JSON: {e}") from e
    if not isinstance(result, dict):
        raise ExtractionError(f"expected a JSON object, got {type(result).__name__}")
    for _, ev in _iter_raw_evidence(result):
        ev["document_id"] = pdf_path.name  # filled here, not by the model
    return result, usage


def _norm(s: str) -> str:
    """Normalise text for quote matching: ignore whitespace, case and unicode variants."""
    s = unicodedata.normalize("NFKC", s).replace("℃", "°C").lower()  # ℃ -> °C
    # Some PDFs' text layers leak raw glyph names: "/Oslash.sc" for Ø, "Speci/f_ications" for fi.
    s = re.sub(r"/oslash(?:\.[a-z]+)?", "ø", s)
    s = re.sub(r"/((?:f|i|l|t)(?:_(?:f|i|l|t))+)", lambda m: m.group(1).replace("_", ""), s)
    return re.sub(r"\s+", "", s)


def verify_quotes(result: dict, pdf_path: Path) -> tuple[list[str], int]:
    """Check each filled field's quote appears on the page it names.

    Quotes with '...' are split and every part must appear on that page.
    Returns (problems, number_of_quotes_checked); problems is empty if all quotes check out.
    """
    pages = [_norm(p.extract_text() or "") for p in PdfReader(pdf_path).pages]
    problems, checked = [], 0
    for key, field in _iter_raw_evidence(result):
        quote, page = field.get("quote"), field.get("page")
        if field.get("value") is None and quote is None:
            continue  # stated as absent: nothing to verify
        checked += 1
        if not isinstance(quote, str) or not isinstance(page, int):
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
    return problems, checked


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
            rec["verify_failures"], rec["quotes_checked"] = verify_quotes(result, pdf_path)
    except Exception as e:  # one bad file must not stop the batch
        rec["status"] = "failed"
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def run_batch(args: argparse.Namespace) -> int:
    """Process every PDF in args.dir (or just the devset). Returns the exit code (1 if anything failed)."""
    pdfs = sorted(args.dir.glob("*.pdf"))
    if args.devset:
        if not DEVSET_PATH.exists():
            sys.exit(f"{DEVSET_PATH} not found")
        wanted = [entry["file"] for entry in json.loads(DEVSET_PATH.read_text())["files"]]
        missing = sorted(set(wanted) - {p.name for p in pdfs})
        if missing:
            sys.exit(f"Devset files not found in {args.dir}: {', '.join(missing)}")
        pdfs = [p for p in pdfs if p.name in set(wanted)]
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
    parser.add_argument("pdf", type=Path, nargs="?", help="path to the datasheet PDF (omit with --all/--devset)")
    parser.add_argument("--verify", action="store_true",
                        help="after extracting, check each quote appears on its stated page")
    parser.add_argument("--verify-only", action="store_true",
                        help="skip the API call; verify the existing outputs/<name>.json against the PDF")
    parser.add_argument("--all", action="store_true",
                        help="batch mode: process every PDF in --dir, skipping ones that already have output")
    parser.add_argument("--devset", action="store_true",
                        help="batch mode on only the files listed in evals/devset.json")
    parser.add_argument("--dir", type=Path, default=Path("documents"),
                        help="folder of PDFs for --all/--devset (default: documents)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="in batch mode: make at most N new API calls this run")
    parser.add_argument("--force", action="store_true",
                        help="in batch mode: re-extract even if outputs/<name>.json exists")
    args = parser.parse_args()

    batch = args.all or args.devset
    if batch == (args.pdf is not None):
        parser.error("give either a PDF path or --all/--devset (not both, not neither)")
    if not batch and (args.limit is not None or args.force):
        parser.error("--limit and --force only apply with --all or --devset")
    if batch:
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
        problems, checked = verify_quotes(result, pdf_path)
        for problem in problems:
            print(f"VERIFY FAILED: {problem}", file=sys.stderr)
        print(f"Verified {checked - len(problems)}/{checked} quotes against {pdf_path}", file=sys.stderr)
        if problems:
            sys.exit(1)


if __name__ == "__main__":
    main()
