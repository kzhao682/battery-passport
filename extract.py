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
import re
import sys
import unicodedata
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

load_dotenv()  # reads ANTHROPIC_API_KEY from .env

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


def extract(pdf_path: Path) -> dict:
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
        sys.exit("Response was cut off at max_tokens; raise max_tokens in extract().")

    # The response may begin with a thinking block; take the text block.
    text = next(b.text for b in response.content if b.type == "text").strip()
    # Claude sometimes wraps JSON in ``` fences; strip them if present.
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]

    usage = response.usage
    print(f"Tokens: {usage.input_tokens} in, {usage.output_tokens} out", file=sys.stderr)
    return json.loads(text)


def _norm(s: str) -> str:
    """Normalise text for quote matching: ignore whitespace, case and unicode variants."""
    s = unicodedata.normalize("NFKC", s).replace("℃", "°C")  # ℃ -> °C
    return re.sub(r"\s+", "", s).lower()


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract battery passport fields from a datasheet PDF.")
    parser.add_argument("pdf", type=Path, help="path to the datasheet PDF")
    parser.add_argument("--verify", action="store_true",
                        help="after extracting, check each quote appears on its stated page")
    parser.add_argument("--verify-only", action="store_true",
                        help="skip the API call; verify the existing outputs/<name>.json against the PDF")
    args = parser.parse_args()

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
        result = extract(pdf_path)
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
