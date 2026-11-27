import json

from battery_passport import extract_v1 as v1
from battery_passport.labels import field_paths


def test_prompt_names_all_25_fields_and_marks_lists():
    for path, is_list in field_paths():
        name = path.split(".")[1]
        assert f"- {name} ({'list' if is_list else 'single'})" in v1.PROMPT_V1


def test_extract_v1_parses_fenced_json_and_stamps_document_id(monkeypatch, tmp_path):
    reply = {"general": {"model": {"value": "X", "unit": None}}, "performance": {"rated_capacity": [{"value": 1, "unit": "Ah"}]}}
    monkeypatch.setattr(v1, "ask_claude", lambda pdf, prompt: ("```json\n" + json.dumps(reply) + "\n```", {"input_tokens": 1, "output_tokens": 2}))
    result, usage = v1.extract_v1(tmp_path / "a.pdf")
    assert result["general"]["model"]["document_id"] == "a.pdf"
    assert result["performance"]["rated_capacity"][0]["document_id"] == "a.pdf"
    assert usage["output_tokens"] == 2
