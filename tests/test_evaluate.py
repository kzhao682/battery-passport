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


def test_wording_differences_do_not_count_as_wrong_for_descriptive_fields():
    path = "materials.chemistry"
    for gold, out in [
        ("LFP (lithium iron phosphate)", "Lithium iron phosphate (LFP)"),
        ("LFP", "LFP (lithium iron phosphate)"),
        ("Na-ion", "Sodium-ion (Na-ion)"),
    ]:
        assert score_field(e(gold), e(out), path)["correct"] == 1, (gold, out)
    assert score_field(e("Shenzhen Z Co.,Ltd."), e("Shenzhen Z Co., Ltd. (深圳)"), "general.manufacturer")["correct"] == 1


def test_loose_matching_still_rejects_different_things():
    assert score_field(e("GWL a.s."), e("Winston"), "general.manufacturer")["wrong"] == 1
    assert score_field(e("Cell, prismatic"), e("LYP type battery cell"), "general.battery_type")["wrong"] == 1
    assert score_field(e("NMC"), e("LFP"), "materials.chemistry")["wrong"] == 1


def test_model_number_stays_strict_and_unpathed_calls_stay_strict():
    assert score_field(e("LF280K"), e("LF280K-2"), "general.model")["wrong"] == 1
    assert score_field(e("LFP"), e("LFP (lithium iron phosphate)"))["wrong"] == 1


def test_ohm_symbol_and_spelled_out_unit_match():
    for symbol in ("mΩ", "mΩ"):  # Greek capital omega and the ohm sign
        assert score_field(e(0.25, "mOhm"), e(0.25, symbol))["correct"] == 1
    assert score_field(e(0.25, "mOhm"), e(0.25, "Ω"))["wrong"] == 1
