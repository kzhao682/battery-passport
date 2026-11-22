"""Plain-Python checks on an extraction result (no AI): schema shape and quote placement."""

import logging
import re
import unicodedata
from pathlib import Path

from pydantic import ValidationError
from pypdf import PdfReader

from .schema import NUMERIC_PATHS, DatasheetExtraction

logging.getLogger("pypdf").setLevel(logging.ERROR)  # silence per-font encoding warnings


def iter_raw_evidence(result: dict):
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
    for key, field in iter_raw_evidence(result):
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
