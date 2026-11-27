"""Batch and single-file logic with the Claude call mocked out (no API, no real PDFs)."""

import argparse
import json
from pathlib import Path

import pytest

from battery_passport import cli
from battery_passport.extract import ExtractionError

CANNED = {"general": {"manufacturer": {"value": "EVE", "page": 1, "quote": "EVE"}}}
USAGE = {"input_tokens": 10, "output_tokens": 5}


@pytest.fixture
def work(tmp_path, monkeypatch):
    """A temp repo root with documents/a.pdf, b.pdf, c.pdf (empty placeholders: extract is mocked)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "documents").mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / "documents" / name).write_bytes(b"")
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    """Replace the Claude call; records which files were 'sent'."""
    sent = []

    def fake_extract(pdf):
        sent.append(pdf.name)
        return json.loads(json.dumps(CANNED)), dict(USAGE)

    monkeypatch.setattr(cli, "extract", fake_extract)
    return sent


def batch_args(**overrides):
    args = dict(dir=Path("documents"), devset=False, all=True, limit=None, force=False,
                verify=False, verify_only=False)
    return argparse.Namespace(**{**args, **overrides})


def process(pdf, out="outputs", **kw):
    opts = dict(verify=False, verify_only=False, force=False)
    return cli.process_pdf(Path(pdf), Path(out), **{**opts, **kw})


def test_process_pdf_extracts_then_reuses(work, calls):
    first = process("documents/a.pdf")
    assert first["status"] == "extracted" and first["input_tokens"] == 10
    assert (work / "outputs" / "a.json").exists()
    second = process("documents/a.pdf")
    assert second["status"] == "reused" and calls == ["a.pdf"]  # no second API call


def test_process_pdf_force_re_extracts(work, calls):
    process("documents/a.pdf")
    process("documents/a.pdf", force=True)
    assert calls == ["a.pdf", "a.pdf"]


def test_verify_only_without_saved_output_is_missing(work, calls):
    assert process("documents/a.pdf", verify_only=True)["status"] == "missing"
    assert calls == []


def test_extraction_error_becomes_failed_not_a_crash(work, monkeypatch):
    def boom(pdf):
        raise ExtractionError("boom")

    monkeypatch.setattr(cli, "extract", boom)
    rec = process("documents/a.pdf")
    assert rec["status"] == "failed" and "boom" in rec["error"]


def test_verify_results_are_recorded(work, calls, monkeypatch):
    monkeypatch.setattr(cli, "verify_quotes", lambda result, pdf: (["bad quote"], 3))
    rec = process("documents/a.pdf", verify=True)
    assert rec["verify_failures"] == ["bad quote"] and rec["quotes_checked"] == 3


def test_batch_limit_counts_only_new_calls_and_resumes(work, calls):
    assert cli.run_batch(batch_args(limit=2)) == 0
    assert calls == ["a.pdf", "b.pdf"]
    cli.run_batch(batch_args(limit=2))  # a, b reused (free); c is the only new call
    assert calls == ["a.pdf", "b.pdf", "c.pdf"]
    summary = json.loads((work / "outputs" / "_batch_summary.json").read_text())
    assert [r["status"] for r in summary] == ["reused", "reused", "extracted"]


def test_batch_continues_after_a_failed_file(work, monkeypatch):
    sent = []

    def flaky(pdf):
        sent.append(pdf.name)
        if pdf.name == "b.pdf":
            raise ExtractionError("nope")
        return json.loads(json.dumps(CANNED)), dict(USAGE)

    monkeypatch.setattr(cli, "extract", flaky)
    assert cli.run_batch(batch_args()) == 1  # exit code 1: something failed
    assert sent == ["a.pdf", "b.pdf", "c.pdf"]
    summary = json.loads((work / "outputs" / "_batch_summary.json").read_text())
    assert [r["status"] for r in summary] == ["extracted", "failed", "extracted"]


def test_devset_limits_the_batch_to_listed_files(work, calls):
    (work / "evals").mkdir()
    (work / "evals" / "devset.json").write_text(json.dumps({"files": [{"file": "b.pdf"}]}))
    cli.run_batch(batch_args(devset=True, all=False))
    assert calls == ["b.pdf"]


def test_devset_with_a_missing_file_exits_with_a_clear_message(work, calls):
    (work / "evals").mkdir()
    (work / "evals" / "devset.json").write_text(json.dumps({"files": [{"file": "nope.pdf"}]}))
    with pytest.raises(SystemExit) as exc:
        cli.run_batch(batch_args(devset=True, all=False))
    assert "nope.pdf" in str(exc.value) and calls == []
