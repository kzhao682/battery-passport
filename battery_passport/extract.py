"""Prompt construction and the Claude call: one datasheet PDF in, raw result dict out."""

import base64
import json
from pathlib import Path
from typing import get_origin

import anthropic
from dotenv import load_dotenv

from .schema import DatasheetExtraction, Evidence
from .validate import iter_raw_evidence

load_dotenv()  # reads ANTHROPIC_API_KEY from .env

MODEL = "claude-sonnet-5-5"


def _field_listing(groups: list[str] | None = None) -> str:
    """Render the schema's groups and fields (with descriptions) as a compact list for the prompt."""
    lines = []
    for group, group_info in DatasheetExtraction.model_fields.items():
        if groups is not None and group not in groups:
            continue
        lines.append(f'"{group}":')
        for name, info in group_info.annotation.model_fields.items():
            shape = (
                "list of evidence objects"
                if get_origin(info.annotation) is list
                else "one evidence object"
            )
            lines.append(f"  - {name} ({shape}): {info.description}")
    return "\n".join(lines)


def _evidence_listing() -> str:
    """Describe the keys of one evidence object, from Evidence's own field descriptions."""
    return "\n".join(
        f"  - {name}: {info.description}"
        for name, info in Evidence.model_fields.items()
        if name != "document_id"  # filled in by this script, not by the model
    )


REPLY_ALL = f"""Reply with JSON only, no other text, in this shape (all groups present):
{{
  "general": {{"manufacturer": {{"value": "...", "unit": null, "page": 1, "quote": "...", "confidence": "high"}}, "model": null, ...}},
  "materials": {{...}},
  "performance": {{"rated_capacity": [{{"value": 280, "unit": "Ah", "conditions": "0.5C", "page": 6, "quote": "..."}}], ...}},
  "durability": {{...}},
  "operating_conditions": {{...}}
}}"""


def build_prompt(groups: list[str] | None = None) -> str:
    """The extraction prompt for all groups (default), or for just the named groups (v2 calls one group at a time)."""
    if groups is None:
        reply = REPLY_ALL
    else:
        reply = (
            f"Reply with JSON only, no other text: the object for the group {', '.join(groups)} "
            "(its fields as keys, no wrapper)."
        )
    return f"""You are extracting data for an EU battery passport from a battery datasheet.

The output has these groups and fields. A field marked "one evidence object" is an object, or
null if the document does not state it. A field marked "list of evidence objects" is a list with
one object per set of conditions, or [] if the document does not state it.

{_field_listing(groups)}

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

{reply}"""


PROMPT = build_prompt()


class ExtractionError(Exception):
    """The model response could not be turned into a result (e.g. truncated)."""


def ask_claude(pdf_path: Path, prompt: str, max_tokens: int = 8192) -> tuple[str, dict]:
    """Send one PDF plus a prompt to Claude. Returns (response text, usage). The only API call site."""
    pdf_data = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
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
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    if response.stop_reason == "max_tokens":
        raise ExtractionError("response was cut off at max_tokens; raise max_tokens in extract()")

    # The response may begin with a thinking block; take the text block.
    text = next(b.text for b in response.content if b.type == "text").strip()
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return text, usage


def parse_json_object(text: str) -> dict:
    """Parse the model's reply as a JSON object, tolerating ``` fences."""
    # Claude sometimes wraps JSON in ``` fences; strip them if present.
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"response was not valid JSON: {e}") from e
    if not isinstance(result, dict):
        raise ExtractionError(f"expected a JSON object, got {type(result).__name__}")
    return result


def extract(pdf_path: Path) -> tuple[dict, dict]:
    """Send one PDF to Claude. Returns (result, {"input_tokens": n, "output_tokens": n})."""
    text, usage = ask_claude(pdf_path, PROMPT)
    result = parse_json_object(text)
    for _, ev in iter_raw_evidence(result):
        ev["document_id"] = pdf_path.name  # filled here, not by the model
    return result, usage
