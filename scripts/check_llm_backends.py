"""Prove each configured LLM backend can actually return structured output.

Extraction fails in a confusing way when a key is present but the backend
cannot serve schema-constrained output — you get "no structured output" 75
times and a burnt budget. This sends ONE tiny schema-constrained request per
configured backend and reports what came back, so a misconfiguration costs a
few seconds instead of a document.

Run:
    python scripts/check_llm_backends.py
    python scripts/check_llm_backends.py --backend huggingface
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ai.router import (  # noqa: E402
    _EXTRACTION_MODELS,
    BackendUnavailable,
    ModelOutputError,
    _extraction_chain,
    _run_one,
    open_weights_only,
)

# Deliberately trivial: this tests the TRANSPORT and schema support, not model
# quality. A backend that cannot do this cannot do questionnaire extraction.
_PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "qid": {"type": "string"},
        "type": {"type": "string"},
        "n_options": {"type": "integer"},
    },
    "required": ["qid", "type", "n_options"],
}

_PROBE_PROMPT = (
    "Extract this survey question into the schema.\n\n"
    "Q7. Which of these brands do you use? 1) Colgate 2) Pepsodent 3) None\n\n"
    "qid is the question id, type is 'single' or 'multi', n_options is how many "
    "answer options there are."
)


def probe(backend: str, model: str, timeout: int = 45) -> tuple[bool, str]:
    t0 = time.perf_counter()
    try:
        response = _run_one(backend, model, _PROBE_PROMPT, _PROBE_SCHEMA, timeout)
    except BackendUnavailable as exc:
        kind = "NOT CONFIGURED" if getattr(exc, "config_error", False) else "UNREACHABLE"
        return False, f"{kind}: {exc}"
    except ModelOutputError as exc:
        # The important failure: reachable, but cannot honour a schema.
        return False, f"NO STRUCTURED OUTPUT: {exc}"
    except Exception as exc:  # noqa: BLE001 - a probe must never itself crash
        return False, f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - t0
    got = response.content
    ok = got.get("n_options") == 3
    detail = f"{elapsed:5.1f}s  {got}"
    return ok, detail if ok else f"{detail}   (expected n_options=3)"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", help="Probe only this backend")
    p.add_argument("--timeout", type=int, default=45)
    args = p.parse_args()

    print(f"open-weights-only mode: {'ON' if open_weights_only() else 'OFF'}")
    chain = _extraction_chain()
    print(f"extraction chain: {' -> '.join(c['backend'] for c in chain)}\n")

    steps = [c for c in chain if not args.backend or c["backend"] == args.backend]
    if args.backend and not steps:
        steps = [{"backend": args.backend,
                  "model": _EXTRACTION_MODELS.get(args.backend, "")}]
        print(f"(‘{args.backend}’ is not in the current chain — probing anyway)\n")

    results = []
    for step in steps:
        backend, model = step["backend"], step["model"]
        label = f"{backend} [{model}]"
        if backend == "huggingface" and (prov := os.environ.get("HF_PROVIDER", "").strip()):
            label += f" via {prov}"
        print(f"probing {label} ...")
        ok, detail = probe(backend, model, args.timeout)
        print(f"  {'PASS' if ok else 'FAIL'}  {detail}\n")
        results.append((backend, ok))

    working = [b for b, ok in results if ok]
    if working:
        print(f"Usable for extraction: {', '.join(working)}")
    else:
        print(
            "No backend can serve structured output.\n"
            "  - Groq:        set GROQ_API_KEY (free tier, 30 req/min, no card)\n"
            "  - HuggingFace: set HF_TOKEN, and try pinning HF_PROVIDER=cerebras\n"
            "  - Ollama:      run `ollama serve` (local, weak — plumbing only)"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
