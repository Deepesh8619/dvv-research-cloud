"""Issue DVV Research Cloud licences. Runs on DVV's machine only.

    python scripts/issue_licence.py keygen
    python scripts/issue_licence.py issue --client "Acme Research" --expires 2027-09-30

The PRIVATE key never enters the repo or an image: it lives in
~/.dvv-keys/licence_signing_key. Back it up in a password manager — without it
no new licence can be issued (recovery = a new keypair + a new release). The
PUBLIC key is pasted into api/licence.py, which is all a running copy needs.
"""

import argparse
import base64
import json
import sys
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_PATH = Path.home() / ".dvv-keys" / "licence_signing_key"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign(private_key: Ed25519PrivateKey, client: str, expires: date) -> str:
    """`<payload>.<signature>`, both base64url. The signature covers the payload text."""
    payload = _b64(json.dumps({"client": client, "expires": expires.isoformat()}).encode())
    return f"{payload}.{_b64(private_key.sign(payload.encode()))}"


def keygen() -> None:
    if KEY_PATH.exists():
        sys.exit(f"{KEY_PATH} already exists — refusing to overwrite a signing key.")
    key = Ed25519PrivateKey.generate()
    KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    KEY_PATH.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    KEY_PATH.chmod(0o600)
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print(f"Private key written to {KEY_PATH} (back it up; never commit it).")
    print(f'PUBLIC_KEY = "{_b64(public)}"  # paste into api/licence.py')


def issue(client: str, expires: str) -> None:
    key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    print(sign(key, client, date.fromisoformat(expires)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen")
    p = sub.add_parser("issue")
    p.add_argument("--client", required=True)
    p.add_argument("--expires", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    keygen() if args.cmd == "keygen" else issue(args.client, args.expires)
