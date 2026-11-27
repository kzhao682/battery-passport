"""v1 baseline (Day 4): one Claude call per datasheet, a plain prompt, free-form JSON.

Deliberately simple so v2 (Day 5) has something to beat: the field names but none of the
schema descriptions or value rules, no page numbers, no quotes. The reply keeps the same
group/field layout as the real schema so evaluate.py can score it unchanged.
Outputs go to outputs/v1/ (the v0 outputs in outputs/ are left alone).
"""

from pathlib import Path
from typing import get_origin

from .extract import ask_claude, parse_json_object
from .schema import DatasheetExtraction

V1_OUT_DIR = Path("outputs/v1")


def _field_names() -> str:
    lines = []
    for group, group_info in DatasheetExtraction.model_fields.items():
        lines.append(f"{group}:")
        for name, info in group_info.annotation.model_fields.items():
            kind = "list" if get_origin(info.annotation) is list else "single"
            lines.append(f"  - {name} ({kind})")
    return "\n".join(lines)


PROMPT_V1 = f"""Extract these fields from the attached battery datasheet.

{_field_names()}

Reply with JSON only, grouped as above. Each field is {{"value": ..., "unit": ..., "conditions": ...}}.
A "list" field is a list of such objects, one per set of conditions. Use null for a single field
(or [] for a list) when the datasheet does not give it."""


def extract_v1(pdf_path: Path) -> tuple[dict, dict]:
    """Returns (result, usage) like extract.extract(), but with the plain v1 prompt."""
    text, usage = ask_claude(pdf_path, PROMPT_V1)
    result = parse_json_object(text)
    for group in result.values():
        if isinstance(group, dict):
            for item in group.values():
                for ev in item if isinstance(item, list) else [item]:
                    if isinstance(ev, dict):
                        ev["document_id"] = pdf_path.name
    return result, usage
