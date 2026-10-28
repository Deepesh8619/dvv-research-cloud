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
