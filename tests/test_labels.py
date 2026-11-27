from battery_passport.labels import field_paths, make_template


def test_template_covers_all_25_fields():
    paths = field_paths()
    assert len(paths) == 25
    t = make_template("X.pdf")
    for path, is_list in paths:
        group, name = path.split(".")
        assert isinstance(t[group][name], list) == is_list


def test_list_fields_match_schema():
    lists = {p for p, is_list in field_paths() if is_list}
    assert "performance.rated_capacity" in lists
    assert "general.weight" not in lists
