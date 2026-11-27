"""v2 extractor (Day 5): the v0 prompt rules, but schema-enforced replies, one call per group.

- Structured outputs: each reply must match one group model derived from schema.py.
  Probing the API showed two limits: the whole DatasheetExtraction in one request is rejected
  ("compiled grammar is too large"), and so is a group whose Evidence keys are optional with
  defaults ("Schema is too complex"). Group by group, with every key REQUIRED but nullable, the
  full value union (number | text | Range | Dimensions) compiles. So v2 makes one call per group
  (as the blueprint plans: one call per sub-model) with an API-side schema built from
  schema.py by api_group_model(). Same keys and meaning, only "required but nullable".
- No prompt caching: tried with cache_control on the PDF block, but on a 5-call run the cache was
  written five times and never read (likely because each call has a different output schema), so
  it only added the cache-write premium. Each call re-sends the PDF: about 5x v0's input tokens.
- Same prompt rules as extract.py (build_prompt), so a v0 -> v2 change comes from the mechanics.
Outputs go to outputs/v2/. Quote verification (validate.verify_quotes) runs as for v0.
"""

import base64
from functools import cache
from pathlib import Path
from typing import Literal, Optional, Union, get_origin

import anthropic
from pydantic import ConfigDict, create_model

from .extract import MODEL, ExtractionError, build_prompt
from .schema import DatasheetExtraction, Dimensions, Evidence, Range
from .validate import iter_raw_evidence

V2_OUT_DIR = Path("outputs/v2")
USAGE_KEYS = ("input_tokens", "output_tokens")


_STRICT = ConfigDict(extra="forbid")


@cache
def _api_evidence():
    """Evidence for the API: same keys as schema.Evidence minus document_id (set by the pipeline)."""
    rng = create_model("Range", __config__=_STRICT, **{k: (Optional[float], ...) for k in Range.model_fields})
    dims = create_model("Dimensions", __config__=_STRICT, **{k: (Optional[float], ...) for k in Dimensions.model_fields})
    return create_model(
        "Evidence",
        __config__=_STRICT,
        value=(Union[float, str, rng, dims, None], ...),
        **{
            k: (i.annotation, ...)
            for k, i in Evidence.model_fields.items()
            if k not in ("value", "document_id")
        },
    )


@cache
def api_group_model(group: str):
    """The structured-output model for one group of DatasheetExtraction (e.g. 'performance')."""
    src = DatasheetExtraction.model_fields[group].annotation
    ev = _api_evidence()
    fields = {
        name: (list[ev] if get_origin(info.annotation) is list else Optional[ev], ...)
        for name, info in src.model_fields.items()
    }
    return create_model(src.__name__, __config__=_STRICT, **fields)


def extract_v2(pdf_path: Path) -> tuple[dict, dict]:
    """Returns (result, usage summed over the group calls)."""
    pdf_data = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")
    client = anthropic.Anthropic()
    result: dict = {}
    usage = dict.fromkeys(USAGE_KEYS, 0)
    for group in DatasheetExtraction.model_fields:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data},
                        },
                        {"type": "text", "text": build_prompt([group])},
                    ],
                }
            ],
            output_format=api_group_model(group),
        )
        if response.stop_reason in ("max_tokens", "refusal"):
            raise ExtractionError(f"{group}: stop_reason={response.stop_reason}; output may not match the schema")
        if response.parsed_output is None:
            raise ExtractionError(f"{group}: no parsed output returned")
        result[group] = response.parsed_output.model_dump(mode="json")
        for key in USAGE_KEYS:
            usage[key] += getattr(response.usage, key, 0) or 0
    for _, ev in iter_raw_evidence(result):
        ev["document_id"] = pdf_path.name  # filled here, not by the model
    return result, usage
