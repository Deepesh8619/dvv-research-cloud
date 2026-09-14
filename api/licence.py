"""Per-client licence: who may run this copy, and until when.

A licence is `<payload>.<signature>` (base64url), the payload being
{"client", "expires"} and the signature Ed25519 over the payload text. Only the
PUBLIC key lives here, so a copy of the image cannot mint its own licence —
issuing needs DVV's private key (scripts/issue_licence.py).

When a licence lapses nothing breaks at once: a GRACE_DAYS window keeps
everything working while it is renewed. After that the management API refuses
with 402, but respondent survey links and public share links keep working —
a live field must never die over an invoice, the respondents did nothing wrong.
"""

import base64
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY = "FoIb7JdvseUdvF50-BWJwLXSoMNUf-0tEW9mjD5ZWvk"
GRACE_DAYS = 14

#: Paths that keep working after a licence lapses (see module docstring).
ALWAYS_OPEN = ("/api/health", "/api/r/", "/api/public/")


class LicenceError(ValueError):
    """The licence was altered, malformed, or not issued by DVV."""


@dataclass(frozen=True)
class Licence:
    client: str
    expires: date

    def state(self, today: date | None = None) -> str:
        today = today or date.today()
        if today <= self.expires:
            return "valid"
        if today <= self.expires + timedelta(days=GRACE_DAYS):
            return "grace"
        return "expired"


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify(token: str, public_key: str = PUBLIC_KEY) -> Licence:
    """Check the signature, then read the payload. Raises LicenceError."""
    try:
        payload, signature = token.strip().split(".")
        Ed25519PublicKey.from_public_bytes(_b64d(public_key)).verify(
            _b64d(signature), payload.encode()
        )
        data = json.loads(_b64d(payload))
        return Licence(client=str(data["client"]), expires=date.fromisoformat(data["expires"]))
    except (ValueError, KeyError, TypeError, InvalidSignature) as exc:
        raise LicenceError(
            "DVV_LICENCE is not valid: it was altered, or not issued by DVV"
        ) from exc


def load(database_url: str) -> Licence | None:
    """The licence this process runs under, or None for local development.

    A real database without a licence refuses to boot, exactly like a missing
    ADMIN_PASSWORD: better a deploy that fails loudly than a copy running
    unlicensed. An EXPIRED licence still boots — see the module docstring.
    """
    token = os.getenv("DVV_LICENCE", "").strip()
    if not token:
        if database_url.startswith("sqlite"):
            return None
        raise RuntimeError("DVV_LICENCE is not set. Every client deployment needs one.")
    return verify(token)
