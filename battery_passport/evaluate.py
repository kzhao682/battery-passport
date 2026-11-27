"""Score saved extractions against the hand-labelled gold set (Day 3). Free: no API, no PDFs.

    .venv/bin/python -m battery_passport.evaluate              # all files in evals/labels/
    .venv/bin/python -m battery_passport.evaluate --outputs-dir outputs/v1

Gold rule: `value: null` (or an empty list) means "the datasheet does not state it", and the
model should return null / []. `in_document` in label files is ignored.

Per field the outcome is one of:
  correct       gold has a value, model has the same value (and unit)
  wrong         both have a value, they differ
  missed        gold has a value, model returned null
  hallucinated  gold is null, model returned a value
  both_null     gold null, model null (counted as correct)
Descriptive text fields (LOOSE_TEXT_PATHS) match on meaning, not exact wording: see loosely_equal.
battery_type is looser still: only the form factor and cell vs module/pack count (same_battery_type).
List fields are matched entry by entry on value + unit; unmatched gold entries are missed,
unmatched model entries are hallucinated. Units are compared as stated; no conversion (Day 6).
"""

import json
import re
import sys
from pathlib import Path

from .labels import LABELS_DIR, field_paths

# Descriptive text fields where wording differs but meaning is the same ("LFP" vs
# "Lithium iron phosphate (LFP)"). general.model stays strict: "LF280K" must not match "LF280K-2".
LOOSE_TEXT_PATHS = {
    "general.manufacturer",
    "general.battery_type",
    "materials.chemistry",
    "materials.cathode_material",
    "materials.anode_material",
    "materials.electrolyte",
}
SYNONYMS = {
    "lithium iron phosphate": "lfp",
    "sodium-ion": "na-ion",
    "sodium ion": "na-ion",
    "nickel manganese cobalt": "nmc",
    "lithium-ion": "li-ion",
    "lithium ion": "li-ion",
}

# general.battery_type only needs the same form factor (and no module/pack vs cell conflict).
FORM_FACTORS = {"prismatic", "cylindrical", "pouch"}
ASSEMBLIES = {"module", "pack"}

OUTPUTS_DIR = Path("outputs")
RESULTS_DIR = Path("evals/results")
REL_TOL = 1e-6


# ---------- comparing values ----------

def _text(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def _unit(u) -> str:
    # casefold turns both "Ω" (U+03A9) and the ohm sign (U+2126) into "ω", so "mΩ" == "mOhm"
    return _text(u or "").replace("℃", "°c").replace("degc", "°c").replace("ω", "ohm")


def _tokens(s) -> set[str]:
    t = _text(s)
    for long, short in SYNONYMS.items():
        t = t.replace(long, short)
    return set(re.findall(r"[^\W_]+", t))  # letters/digits only; keeps Chinese characters


def loosely_equal(a, b) -> bool:
    """Same meaning, different wording: one string's words are all contained in the other's."""
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def same_battery_type(gold, out) -> bool:
    """Gold "Cell, cylindrical" matches "Cylindrical rechargeable lithium ion battery": the form
    factor must agree, and one side must not say module/pack when the other doesn't. If the gold
    label names no form factor, fall back to the general wording rule."""
    tg, to = _tokens(gold), _tokens(out)
    if not tg & FORM_FACTORS:
        return loosely_equal(gold, out)
    return tg & FORM_FACTORS == to & FORM_FACTORS and tg & ASSEMBLIES == to & ASSEMBLIES


def values_equal(a, b, loose: bool = False) -> bool:
    """Numbers within tolerance; dicts (Range/Dimensions) key by key; text case/space-insensitive."""
    if isinstance(a, dict) and isinstance(b, dict):
        keys = {k for k, v in {**a, **b}.items() if a.get(k) is not None or b.get(k) is not None}
        return all(values_equal(a.get(k), b.get(k)) for k in keys)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= REL_TOL * max(1.0, abs(a), abs(b))
    if _text(a) == _text(b):
        return True
    return loose and isinstance(a, str) and isinstance(b, str) and loosely_equal(a, b)


def entries_match(gold: dict, out: dict, path: str | None = None) -> bool:
    a, b = gold.get("value"), out.get("value")
    if path == "general.battery_type" and isinstance(a, str) and isinstance(b, str):
        if not same_battery_type(a, b):
            return False
    elif not values_equal(a, b, loose=path in LOOSE_TEXT_PATHS):
        return False
    # Unit only matters when the gold label states one.
    return not gold.get("unit") or _unit(gold["unit"]) == _unit(out.get("unit"))


def _has_value(e) -> bool:
    return isinstance(e, dict) and e.get("value") is not None


def _as_list(x) -> list[dict]:
    if x is None:
        return []
    return [e for e in (x if isinstance(x, list) else [x]) if _has_value(e)]


# ---------- scoring one field / one file ----------

def score_field(gold, out, path: str | None = None) -> dict:
    """Return {'correct','wrong','missed','hallucinated','both_null','page_ok','page_total'} counts."""
    g, o = _as_list(gold), _as_list(out)
    r = dict(correct=0, wrong=0, missed=0, hallucinated=0, both_null=0, page_ok=0, page_total=0)
    if not g and not o:
        r["both_null"] = 1
        return r
    unused = list(o)
    leftover_gold = []
    for ge in g:
        hit = next((oe for oe in unused if entries_match(ge, oe, path)), None)
        if hit is None:
            leftover_gold.append(ge)
            continue
        unused.remove(hit)
        r["correct"] += 1
        if ge.get("page") is not None:
            r["page_total"] += 1
            r["page_ok"] += ge["page"] == hit.get("page")
    # Leftovers pair up as wrong; remaining gold is missed, remaining output is hallucinated.
    paired = min(len(leftover_gold), len(unused))
    r["wrong"] += paired
    r["missed"] += len(leftover_gold) - paired
    r["hallucinated"] += len(unused) - paired
    return r


def score_file(gold: dict, out: dict) -> dict[str, dict]:
    """{field path: score_field result} for all 25 fields."""
    results = {}
    for path, _ in field_paths():
        group, name = path.split(".")
        results[path] = score_field(gold.get(group, {}).get(name), out.get(group, {}).get(name), path)
    return results


def summarize(per_field: dict[str, dict]) -> dict:
    keys = ("correct", "wrong", "missed", "hallucinated", "both_null", "page_ok", "page_total")
    t = {k: sum(r[k] for r in per_field.values()) for k in keys}
    gold_n = t["correct"] + t["wrong"] + t["missed"]
    out_n = t["correct"] + t["wrong"] + t["hallucinated"]
    t["recall"] = t["correct"] / gold_n if gold_n else None      # of gold values, how many found right
    t["precision"] = t["correct"] / out_n if out_n else None     # of model values, how many right
    t["page_accuracy"] = t["page_ok"] / t["page_total"] if t["page_total"] else None
    return t


# ---------- running over the gold set ----------

def gold_files(labels_dir: Path = LABELS_DIR) -> list[Path]:
    out = []
    for p in sorted(labels_dir.glob("*.json")):
        if json.loads(p.read_text()).get("labelled"):
            out.append(p)
    return out


def run(labels_dir: Path = LABELS_DIR, outputs_dir: Path = OUTPUTS_DIR) -> dict:
    files, totals = {}, {}
    for lp in gold_files(labels_dir):
        op = outputs_dir / lp.name
        if not op.exists():
            files[lp.stem] = {"error": f"no output at {op}"}
            continue
        per_field = score_file(json.loads(lp.read_text()), json.loads(op.read_text()))
        files[lp.stem] = {"summary": summarize(per_field), "fields": per_field}
        for path, r in per_field.items():
            agg = totals.setdefault(path, {k: 0 for k in r})
            for k, v in r.items():
                agg[k] += v
    scored = [f["fields"] for f in files.values() if "fields" in f]
    overall = summarize(totals) if scored else {}
    return {"overall": overall, "by_field": totals, "files": files}


def fmt(x) -> str:
    return "  n/a" if x is None else f"{x:5.0%}"


def print_report(res: dict) -> None:
    print(f"{'file':32} {'ok':>4} {'wrong':>5} {'miss':>4} {'hall':>4} {'recall':>6} {'prec':>5} {'page':>5}")
    for name, f in res["files"].items():
        if "error" in f:
            print(f"{name:32} {f['error']}")
            continue
        s = f["summary"]
        print(f"{name:32} {s['correct']:4} {s['wrong']:5} {s['missed']:4} {s['hallucinated']:4} "
              f"{fmt(s['recall'])} {fmt(s['precision'])} {fmt(s['page_accuracy'])}")
    o = res["overall"]
    if o:
        print(f"{'OVERALL':32} {o['correct']:4} {o['wrong']:5} {o['missed']:4} {o['hallucinated']:4} "
              f"{fmt(o['recall'])} {fmt(o['precision'])} {fmt(o['page_accuracy'])}")
    bad = {p: r for p, r in res["by_field"].items() if r["wrong"] or r["missed"] or r["hallucinated"]}
    if bad:
        print("\nfields with errors (wrong / missed / hallucinated):")
        for p, r in sorted(bad.items(), key=lambda kv: -(kv[1]["wrong"] + kv[1]["missed"] + kv[1]["hallucinated"])):
            print(f"  {p:42} {r['wrong']} / {r['missed']} / {r['hallucinated']}")


def main(argv: list[str]) -> int:
    outputs_dir = OUTPUTS_DIR
    if "--outputs-dir" in argv:
        outputs_dir = Path(argv[argv.index("--outputs-dir") + 1])
    res = run(outputs_dir=outputs_dir)
    print_report(res)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    target = RESULTS_DIR / f"{outputs_dir.name}.json"
    target.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    print(f"\nsaved {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
