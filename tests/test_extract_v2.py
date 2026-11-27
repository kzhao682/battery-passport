from types import SimpleNamespace
from typing import get_origin

import pytest

from battery_passport import extract_v2 as v2
from battery_passport.extract import ExtractionError
from battery_passport.schema import DatasheetExtraction


class FakeClient:
    def __init__(self, response):
        self.messages = SimpleNamespace(parse=lambda **kw: response)


def _response(parsed, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5)
    return SimpleNamespace(stop_reason=stop, parsed_output=parsed, usage=usage)


def test_extract_v2_one_call_per_group_and_stamped(monkeypatch, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    calls = []

    def parse(**kw):
        calls.append(kw)
        M = kw["output_format"]
        # every field is required in the API schema: build a group with all fields null / []
        data = {n: ([] if get_origin(i.annotation) is list else None) for n, i in M.model_fields.items()}
        if M.__name__ == "GeneralInfo":
            data["model"] = dict.fromkeys(("value", "unit", "conditions", "page", "quote", "confidence", "note"))
            data["model"].update(value="X", page=1, quote="X")
        return _response(M.model_validate(data))

    client = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    monkeypatch.setattr(v2.anthropic, "Anthropic", lambda: client)
    result, usage = v2.extract_v2(pdf)
    assert [c["output_format"].__name__ for c in calls] == [
        "GeneralInfo", "Materials", "Performance", "Durability", "OperatingConditions"
    ]
    assert all("cache_control" not in c["messages"][0]["content"][0] for c in calls)
    assert set(result) == set(DatasheetExtraction.model_fields)
    assert result["general"]["model"]["document_id"] == "a.pdf"
    assert usage == {"input_tokens": 50, "output_tokens": 25}
    # each call's prompt lists only its own group's fields
    assert '"materials":' in calls[1]["messages"][0]["content"][1]["text"]
    assert '"general":' not in calls[1]["messages"][0]["content"][1]["text"]


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_extract_v2_raises_when_output_may_be_invalid(monkeypatch, tmp_path, stop):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(v2.anthropic, "Anthropic", lambda: FakeClient(_response(None, stop)))
    with pytest.raises(ExtractionError):
        v2.extract_v2(pdf)


def test_api_schema_matches_schema_py_and_requires_every_key():
    for group, info in DatasheetExtraction.model_fields.items():
        M = v2.api_group_model(group)
        assert list(M.model_fields) == list(info.annotation.model_fields)
        assert all(f.is_required() for f in M.model_fields.values())
    ev = v2._api_evidence()
    assert "document_id" not in ev.model_fields and "quote" in ev.model_fields
    assert all(f.is_required() for f in ev.model_fields.values())
