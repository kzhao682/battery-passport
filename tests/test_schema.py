import pytest
from pydantic import ValidationError

from battery_passport.extract import PROMPT
from battery_passport.schema import NUMERIC_PATHS, DatasheetExtraction, Dimensions, Evidence, Range


def _groups():
    for group, info in DatasheetExtraction.model_fields.items():
        yield group, info.annotation


def test_empty_extraction_is_valid():
    assert DatasheetExtraction().model_dump()["general"]["manufacturer"] is None


def test_json_schema_builds():
    assert "$defs" in DatasheetExtraction.model_json_schema()


def test_numeric_paths_point_at_real_fields():
    # Catches renaming a field and forgetting NUMERIC_PATHS.
    for path in NUMERIC_PATHS:
        group, name = path.split(".")
        groups = dict(_groups())
        assert group in groups, path
        assert name in groups[group].model_fields, path


def test_prompt_lists_every_schema_field():
    for group, cls in _groups():
        assert f'"{group}":' in PROMPT
        for name in cls.model_fields:
            assert f"- {name} (" in PROMPT, f"{group}.{name} missing from the prompt"


def test_range_value_is_not_mistaken_for_dimensions():
    ev = Evidence.model_validate({"value": {"min": 0, "max": 55}})
    assert isinstance(ev.value, Range)


def test_dimensions_value_includes_diameter_for_cylindrical_cells():
    ev = Evidence.model_validate({"value": {"diameter": 18.3, "height": 65.2}})
    assert isinstance(ev.value, Dimensions) and ev.value.diameter == 18.3


@pytest.mark.parametrize("model", [Range, Dimensions])
def test_range_and_dimensions_reject_unknown_keys(model):
    with pytest.raises(ValidationError):
        model(bogus=1)
