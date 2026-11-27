from battery_passport.validate import iter_raw_evidence, validate

GOOD = {
    "general": {"manufacturer": {"value": "EVE", "page": 1, "quote": "EVE Energy"}},
    "performance": {
        "nominal_voltage": {"value": 3.2, "unit": "V", "page": 6, "quote": "Typical Voltage 3.2V"},
        "internal_resistance": [{"value": {"min": 0.1, "max": 0.6}, "page": 6, "quote": "0.1~0.6"}],
    },
}


def test_good_result_has_no_problems():
    assert validate(GOOD) == []


def test_empty_result_is_valid():
    assert validate({}) == []


def test_null_value_without_quote_is_fine():
    assert validate({"general": {"model": {"value": None}}}) == []


def test_text_where_a_number_is_required_is_flagged():
    bad = {"performance": {"nominal_voltage": {"value": "3.2 V typical", "page": 1, "quote": "x"}}}
    problems = validate(bad)
    assert len(problems) == 1
    assert "performance.nominal_voltage" in problems[0] and "expected a number" in problems[0]


def test_text_is_fine_where_text_is_allowed():
    ok = {"materials": {"chemistry": {"value": "LFP", "page": 1, "quote": "LFP"}}}
    assert validate(ok) == []


def test_value_without_page_or_quote_is_flagged():
    problems = validate({"general": {"manufacturer": {"value": "EVE"}}})
    assert problems == ["general.manufacturer: has a value but no page/quote"]


def test_wrong_key_inside_a_range_is_rejected_once_per_field():
    bad = {"operating_conditions": {"charge_temperature": {"value": {"minn": 0}}}}
    problems = validate(bad)
    # The Union (number | text | Range | Dimensions) reports one error per member; we keep one.
    assert len(problems) == 1
    assert problems[0].startswith("operating_conditions.charge_temperature.value")


def test_iter_raw_evidence_paths_and_malformed_input():
    result = {
        "a": {"x": {"value": 1}, "y": [{"value": 2}, "junk", {"value": 3}], "z": None, "w": 5},
        "b": "not a dict",
    }
    assert [path for path, _ in iter_raw_evidence(result)] == ["a.x", "a.y[0]", "a.y[2]"]
