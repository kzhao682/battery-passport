"""Test setup: these tests must never call the Anthropic API (it costs credits).

An autouse fixture replaces the client class with one that raises, so an accidental real call fails
the test loudly instead of billing. No test needs a PDF or the network either.
"""

import anthropic
import pytest


@pytest.fixture(autouse=True)
def _no_real_api(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("tests must not call the Anthropic API")

    monkeypatch.setattr(anthropic, "Anthropic", _blocked)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
