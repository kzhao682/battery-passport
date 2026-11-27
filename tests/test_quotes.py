"""Quote checking on plain text (check_quotes), so no PDF is needed."""

from battery_passport.validate import check_quotes


def ev(quote, page=1, value=1):
    return {"g": {"f": {"value": value, "page": page, "quote": quote}}}


def test_matches_ignoring_whitespace_and_case():
    assert check_quotes(ev("typical voltage 3.2v"), ["Typical   Voltage\n3.2V"]) == ([], 1)


def test_degree_celsius_symbol_variants_match():
    assert check_quotes(ev("0℃~55℃"), ["Charging Temperature 0°C~55°C"])[0] == []


def test_glyph_name_oslash_matches_diameter_sign():
    # Regression: the pattern once swallowed the digits after "/Oslash.sc".
    assert check_quotes(ev("Ø26 x 65"), ["Cell Dimensions (mm) /Oslash.sc26 x 65"])[0] == []


def test_leaked_ligature_names_match():
    assert check_quotes(ev("Cell Specifications"), ["ANR Cell Speci/f_ications"])[0] == []


def test_ellipsis_quote_needs_every_part_on_the_page():
    page = ["Charging 0 to 55 and some other cells then Discharging -20"]
    assert check_quotes(ev("Charging 0 to 55...Discharging -20"), page)[0] == []
    problems, _ = check_quotes(ev("Charging 0 to 55...Storage 40"), page)
    assert len(problems) == 1


def test_wrong_page_reports_the_right_one():
    problems, checked = check_quotes(ev("Typical Voltage 3.2V", page=1), ["cover", "Typical Voltage 3.2V"])
    assert checked == 1
    assert "not on page 1" in problems[0] and "found on page [2]" in problems[0]


def test_quote_nowhere_in_the_document():
    problems, _ = check_quotes(ev("12.8mΩ"), ["page one", "page two"])
    assert "not found anywhere" in problems[0]


def test_page_out_of_range():
    problems, _ = check_quotes(ev("x", page=5), ["a", "b"])
    assert "outside the document (1-2)" in problems[0]


def test_value_without_quote_or_page_is_a_problem():
    problems, checked = check_quotes({"g": {"f": {"value": 3.2}}}, ["a"])
    assert checked == 1 and "no quote/page" in problems[0]


def test_absent_values_are_skipped():
    result = {"g": {"null": {"value": None, "quote": None}, "empty": [], "none": None}}
    assert check_quotes(result, ["a"]) == ([], 0)


def test_counts_only_filled_fields():
    result = {"g": {"a": {"value": 1, "page": 1, "quote": "one"}, "b": [{"value": 2, "page": 1, "quote": "two"}]}}
    assert check_quotes(result, ["one two"]) == ([], 2)
