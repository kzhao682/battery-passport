"""Command line: extract one datasheet, or batch over a folder, with optional quote verification.

Usage (run from the repo root; paths like documents/, outputs/ and evals/ are relative to it):
    python -m battery_passport documents/X.pdf                # extract -> outputs/X.json
    python -m battery_passport documents/X.pdf --verify       # extract, then check quotes
    python -m battery_passport documents/X.pdf --verify-only  # re-check saved JSON, no API call
    python -m battery_passport --all [--verify] [--limit N]   # every PDF in documents/
    python -m battery_passport --devset [--verify] [--limit N]  # only the files in evals/devset.json
"""

import argparse
import json
import sys
from pathlib import Path

from .extract import ExtractionError, extract
from .validate import validate, verify_quotes

DEVSET_PATH = Path("evals/devset.json")


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
    parser = argparse.ArgumentParser(prog="python -m battery_passport", description="Extract battery passport fields from datasheet PDFs.")
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
