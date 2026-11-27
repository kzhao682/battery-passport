from battery_passport.evaluate import score_field, summarize, values_equal


def e(value, unit=None, page=1, **kw):
    return {"value": value, "unit": unit, "page": page, **kw}


def test_correct_and_page():
    r = score_field(e(3.2, "V", page=6), e(3.2, "V", page=5))
    assert r["correct"] == 1 and r["page_total"] == 1 and r["page_ok"] == 0


def test_wrong_missed_hallucinated():
    assert score_field(e(3.2, "V"), e(3.3, "V"))["wrong"] == 1
    assert score_field(e(3.2, "V"), None)["missed"] == 1
    assert score_field(e(None), e(5, "g"))["hallucinated"] == 1
    assert score_field(e(None), None)["both_null"] == 1


def test_unit_mismatch_is_wrong():
    assert score_field(e(280, "Ah"), e(280, "mAh"))["wrong"] == 1


def test_list_field_matches_entries_independently():
    gold = [e(900, "A"), e(3000, "A")]
    out = [e(3000, "A"), e(900, "A"), e(50, "A")]
    r = score_field(gold, out)
    assert (r["correct"], r["hallucinated"], r["missed"]) == (2, 1, 0)


def test_dimensions_and_range():
    assert values_equal({"width": 1.0, "height": 2.0}, {"height": 2, "width": 1, "length": None})
    assert not values_equal({"min": 0, "max": 55}, {"min": 0, "max": 60})


def test_gold_matches_itself_perfectly():
    g = e(3.2, "V")
    s = summarize({"performance.nominal_voltage": score_field(g, g)})
    assert s["recall"] == 1 and s["precision"] == 1
