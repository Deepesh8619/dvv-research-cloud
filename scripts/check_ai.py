"""Which AI provider answers on this server, and can it use tools?

Run on a client's server after setup:  ./dvv check-ai
Sends only a trivial prompt — never client data.
"""

import sys

from src.ai.copilot import run_copilot
from src.ai.router import llm_key_presence, run_task
from src.engines.rag.embeddings import LEXICAL, embed

vectors, made_by = embed(["how likely are you to recommend us", "net promoter score"], kind="query")
if made_by == LEXICAL:
    print("search embedder: NONE (lexical fallback, wording only). Turn it on: ./dvv embedder")
else:
    a, b = vectors
    print(f"search embedder: {made_by}, {len(a)} dimensions — "
          f"'recommend' vs 'NPS' similarity {sum(x * y for x, y in zip(a, b, strict=True)):.2f}")
if "--embeddings-only" in sys.argv:
    sys.exit(0 if made_by != LEXICAL else 1)

keys = [k for k, present in llm_key_presence().items() if present]
print("AI keys set:", ", ".join(keys) or "none")

schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
extraction = run_task("questionnaire.extract", "Reply with the word ok.", schema, timeout=60)
print(f"questionnaire extraction: answered by {extraction.backend} ({extraction.model})")

used: list[str] = []


def execute(name: str, args: dict) -> dict:
    used.append(name)
    return {"projects": []}


_, _, who = run_copilot("How many projects are there?", [], execute, user="check", role="owner")
print(f"copilot: answered by {who}; tools used: {', '.join(used) or 'none'}")
if used:
    print("PASS")
else:
    print("WARN: the copilot answered without calling a tool. On Bedrock, set in deploy/.env\n"
          "      BEDROCK_BASE_URL=https://bedrock-mantle.<region>.api.aws/v1  then ./dvv up")


# The fallback on its own: the main model is fine most days, so a broken
# fallback would otherwise only be discovered on the day it is needed.
import src.ai.copilot as copilot  # noqa: E402

second = [p for p in copilot._providers() if p[0] == "Bedrock fallback"]
if not second:
    print("copilot fallback: off (set BEDROCK_FALLBACK_MODEL to turn it on)")
else:
    used.clear()
    real = copilot._providers
    copilot._providers = lambda: second
    try:
        _, _, who = run_copilot("How many projects are there?", [], execute, user="check",
                                role="owner")
        print(f"copilot fallback ({second[0][3]}): answered by {who}; "
              f"tools used: {', '.join(used) or 'none'} — {'PASS' if used else 'WARN'}")
    except Exception as exc:  # noqa: BLE001 - say what failed, in AWS's words
        print(f"copilot fallback ({second[0][3]}): FAILED — {exc}\n"
              "      If the model is not offered in this region, either set\n"
              "      BEDROCK_FALLBACK_REGION (client text then leaves this region) or\n"
              "      BEDROCK_FALLBACK_MODEL=none in deploy/.env, then ./dvv up")
    finally:
        copilot._providers = real


# Screenshots pasted into the copilot are read by a vision model. A blank 1x1
# test image — never client data — is enough to learn whether the model exists
# in this region and accepts images.
from src.ai import vision  # noqa: E402

_BLANK = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m"
          "P8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
if not vision.model():
    print("screenshots: off (BEDROCK_VISION_MODEL is empty)")
else:
    try:
        vision.transcribe(_BLANK, timeout=60)
        print(f"screenshots: read by {vision.model()} — PASS")
    except Exception as exc:  # noqa: BLE001 - say what failed, in AWS's words
        print(f"screenshots: FAILED with {vision.model()} — {exc}\n"
              "      Set BEDROCK_VISION_MODEL in deploy/.env to a vision model this region\n"
              "      offers (Bedrock console -> Model catalog -> filter 'Image' input), then\n"
              "      ./dvv up. Until then the copilot says it cannot see screenshots.")
