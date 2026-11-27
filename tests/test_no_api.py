"""Proves the safety net in conftest.py works: the real API path cannot be reached from tests."""

import anthropic
import pytest

from battery_passport.extract import extract


def test_client_is_blocked():
    with pytest.raises(AssertionError, match="must not call"):
        anthropic.Anthropic()


def test_real_extract_cannot_reach_the_api(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")  # extract() reads the bytes, then builds the client
    with pytest.raises(AssertionError, match="must not call"):
        extract(pdf)
