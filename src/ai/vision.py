"""Read a pasted screenshot into text the copilot can reason over.

The copilot's models read text only. A screenshot is therefore transcribed
ONCE, by a vision model on the same in-region Bedrock account, and the
transcript joins the user's message — so every tool, rule and check the copilot
already has applies to it, and the conversation history carries what the image
showed into later turns.

The transcriber only describes what is visible; it never answers the question.
Screenshots are client material, so they go to Bedrock in the client's region
and nowhere else — no free tier, no fallback to another provider.

    BEDROCK_VISION_MODEL   the model ID (empty string turns screenshots off)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from src.ai.router import USER_AGENT, BackendUnavailable, bedrock_chat_url, http_json

DEFAULT_MODEL = "qwen.qwen3-vl-235b-a22b"
MAX_IMAGES = 3
#: ~4 MB of image once decoded; a full-screen retina PNG is well under it.
MAX_DATA_URL = 5_600_000
DATA_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")

PROMPT = (
    "Transcribe this screenshot. It is from a market-research platform or a survey "
    "questionnaire. Write out ALL visible text exactly as shown, top to bottom. Keep "
    "tables as rows with ' | ' between cells. Say which items are selected, highlighted, "
    "struck through or coloured (e.g. red text, a yellow 'Needs you' badge). Begin with "
    "one line saying what kind of screen or document it is. Do not explain, judge or "
    "answer anything — only report what is visible."
)


def model() -> str:
    return os.environ.get("BEDROCK_VISION_MODEL", DEFAULT_MODEL).strip()


def valid(image: str) -> bool:
    return len(image) <= MAX_DATA_URL and bool(DATA_URL.match(image))


def transcribe(image: str, timeout: int = 90) -> str:
    """One screenshot as text. Raises BackendUnavailable when it cannot be read."""
    key = os.environ.get("BEDROCK_API_KEY", "").strip()
    if not key or not model():
        raise BackendUnavailable("no image-reading model is configured "
                                 "(BEDROCK_API_KEY and BEDROCK_VISION_MODEL)", config_error=True)
    request = urllib.request.Request(
        bedrock_chat_url(),
        data=json.dumps({
            "model": model(), "max_tokens": 2000, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": image}},
            ]}],
        }).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                 "User-Agent": USER_AGENT},
    )
    try:
        body = http_json(request, timeout)  # counted in AI usage like every model call
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BackendUnavailable(f"the image-reading model ({model()}) failed: {exc}") from exc
    text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    # Reasoning models may think aloud first; only the transcript is wanted.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text:
        raise BackendUnavailable("the image-reading model returned nothing")
    return text


def with_screenshots(message: str, images: list[str], on_step=None) -> str:
    """The user's message with each screenshot's transcript appended.

    When no image can be read, says so in the message instead, so the copilot
    tells the user plainly rather than answering as if it had seen the image.
    """
    if not images:
        return message
    parts = [message]
    for n, image in enumerate(images[:MAX_IMAGES], 1):
        if on_step:
            on_step(f"Reading screenshot {n}")
        try:
            parts.append(f"[Screenshot {n}, as read]\n{transcribe(image)}\n[End of screenshot {n}]")
        except BackendUnavailable as exc:
            parts.append(f"[Screenshot {n} could not be read: {exc}. Tell the user you could "
                         "not see it, and ask them to paste the text or describe it.]")
    return "\n\n".join(parts)
