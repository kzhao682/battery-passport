"""Blank gold-label templates for the Day 3 hand-labelling (free: no API, no PDF reading).

    .venv/bin/python -m battery_passport.labels          # write missing templates
    .venv/bin/python -m battery_passport.labels --list   # just show which files and paths

One file per `label_day3` datasheet in evals/devset.json -> evals/labels/<name>.json.
Templates are deliberately blank (not pre-filled from outputs/) so the gold set is
labelled from the PDF alone and does not inherit the model's mistakes.

How to fill a label file:
- Single field: set `value` (+ `unit`, `page`). If the datasheet does not state it, set
  `"in_document": false` and leave `value` null. That is a real label: the model must say null.
- List field (`rated_capacity`, `cycle_life`, currents, ...): one entry per test condition,
  each with `conditions`. Empty list = datasheet states none.
- Ranges: `{"min": .., "max": ..}`. Dimensions: `{"length": .., ...}`. Keep units as stated.
- `page` = position in the PDF file (first page = 1), same rule as extraction.
- Never calculate a value; only label what the datasheet states.
"""

import json
import sys
from pathlib import Path
from typing import get_origin

from .schema import DatasheetExtraction

DEVSET = Path("evals/devset.json")
LABELS_DIR = Path("evals/labels")


def field_paths() -> list[tuple[str, bool]]:
    """(dotted path, is_list) for every passport field, in schema order."""
    paths = []
    for group, ginfo in DatasheetExtraction.model_fields.items():
        for name, finfo in ginfo.annotation.model_fields.items():
            paths.append((f"{group}.{name}", get_origin(finfo.annotation) is list))
    return paths


def single_entry() -> dict:
    return {"value": None, "unit": None, "page": None, "in_document": True, "note": None}


def list_entry() -> dict:
    return {"value": None, "unit": None, "conditions": None, "page": None}


def make_template(document_id: str) -> dict:
    out: dict = {"document_id": document_id, "labelled": False}
    for path, is_list in field_paths():
        group, name = path.split(".")
        out.setdefault(group, {})[name] = [list_entry()] if is_list else single_entry()
    return out


def label_files() -> list[str]:
    files = json.loads(DEVSET.read_text())["files"]
    return [f["file"] for f in files if f.get("label_day3")]


def write_templates() -> list[Path]:
    """Write templates that don't exist yet; never overwrite hand-labelled work."""
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for fname in label_files():
        target = LABELS_DIR / f"{Path(fname).stem}.json"
        if target.exists():
            print(f"skip   {target} (exists)")
            continue
        target.write_text(json.dumps(make_template(fname), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote  {target}")
        written.append(target)
    return written


def main() -> int:
    if "--list" in sys.argv:
        for f in label_files():
            print(f)
        for path, is_list in field_paths():
            print(f"  {path}{' []' if is_list else ''}")
        return 0
    write_templates()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
