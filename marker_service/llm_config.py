"""Which Gemini model Marker's `use_llm` accuracy pass calls.

Deliberately free of `marker` imports so it can be unit-tested without the
service's heavyweight dependency tree.

Why this exists rather than a literal in `app.py`: marker-pdf pins its own
default (`gemini-2.0-flash` at 1.10.2), Google retires models on a schedule
nobody here controls, and the failure is silent. When the model went away
every LLM call answered 404; Marker logged "LLM did not return a valid
response", returned 200, and `/health` went on reporting `use_llm: true`
while LLMTableProcessor — the pass that keeps table structure honest — never
ran. Naming the model here makes the version explicit and lets a deployment
move without waiting on a release.
"""
from __future__ import annotations

import os

# Direct successor to the retired `gemini-2.0-flash`, and the same API
# generation marker-pdf 1.10.2 builds its structured-output calls against.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

_ENV_VAR = "MARKER_GEMINI_MODEL"


def gemini_model() -> str:
    """The model name to hand Marker, honouring `MARKER_GEMINI_MODEL`.

    An unset compose variable arrives as an empty string, which must fall back
    to the default rather than ask Gemini for a model named "".
    """
    return (os.environ.get(_ENV_VAR) or "").strip() or DEFAULT_GEMINI_MODEL
