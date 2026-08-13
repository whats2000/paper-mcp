"""The Gemini model name Marker's accuracy pass runs against.

Loaded by path: `marker_service` is a separate package whose `app` module
imports `marker` at module scope, which the service venv does not carry. The
config seam is deliberately free of those imports so it can be tested here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SOURCE = Path(__file__).resolve().parents[1] / "marker_service" / "llm_config.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("marker_llm_config", _SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gemini_model_defaults_to_a_model_google_still_serves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marker-pdf 1.10.2 defaults to `gemini-2.0-flash`, which Google retired.

    Leaving that default in place cost nothing visible and broke everything
    quietly: every LLM call answered 404, Marker logged "LLM did not return a
    valid response" and returned 200, so `use_llm` reported healthy while the
    accuracy pass — including LLMTableProcessor — never ran once.
    """
    monkeypatch.delenv("MARKER_GEMINI_MODEL", raising=False)

    model = _load().gemini_model()

    assert model
    assert model != "gemini-2.0-flash"


def test_gemini_model_can_be_pinned_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Google retires models on their own schedule; a deployment must be able
    # to move without waiting on a release here.
    monkeypatch.setenv("MARKER_GEMINI_MODEL", "gemini-3.5-flash")

    assert _load().gemini_model() == "gemini-3.5-flash"


def test_a_blank_env_override_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unset variable in compose arrives as "", which must not become the
    # model name — marker would then request a model called "".
    monkeypatch.setenv("MARKER_GEMINI_MODEL", "   ")
    module = _load()

    assert module.gemini_model() == module.DEFAULT_GEMINI_MODEL
