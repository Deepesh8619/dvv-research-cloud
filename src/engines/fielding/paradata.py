"""What the interview itself says about a respondent, beside their answers.

Two things a client asks for on every study and nobody can answer afterwards:

* **What were they on?** A grid that works on a laptop and not on a phone shows
  up as "mobile respondents drop at Q12", and only if the device was recorded
  at the time.
* **Are they where they say they are?** A screener that recruits Nassau and
  Queens zip codes is worth nothing if the person is somewhere else. The
  respondent's stated place is compared with where their connection says they
  are, and a disagreement is FLAGGED, never acted on: a VPN, a work laptop and
  a neighbouring postcode are all ordinary, and throwing those interviews away
  silently would be worse than the fraud it is meant to catch.

Location comes from an offline database on the server (MaxMind GeoLite2 City,
path in `DVV_GEOIP_DB`). No respondent's address is ever sent to a third-party
service to be looked up. Without that file everything here still runs and the
location is simply "unknown".

The IP itself is not kept: a salted hash is stored so two sessions from one
connection can be recognised without holding an address. `DVV_STORE_RAW_IP=1`
keeps it in full where a client's own rules demand that.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

# Codes for the data file. Paradata is coded like any other single-coded
# variable so a tab plan can cut by it.
DEVICE_CODES = {"mobile": 1, "tablet": 2, "desktop": 3, "unknown": 99}
PLACE_CODES = {"match": 1, "mismatch": 2, "unknown": 99}

_TABLET = re.compile(r"\b(ipad|tablet|playbook|silk|kindle)\b|android(?!.*\bmobile\b)", re.I)
_MOBILE = re.compile(
    r"\b(iphone|ipod|android.*mobile|windows phone|blackberry|mobile safari)\b", re.I
)
_DESKTOP = re.compile(r"\b(windows nt|macintosh|x11|cros|linux)\b", re.I)

#: "11042", "NW1 6XE" — what a respondent types when asked where they live.
_POSTCODE = re.compile(r"\b\d{5}(?:-\d{4})?\b|\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
_ASKS_POSTCODE = re.compile(r"\b(zip|postal|postcode|post code)\b", re.I)
_ASKS_REGION = re.compile(r"\b(state|province|region|county|city|where do you live)\b", re.I)


def device_of(user_agent: str | None) -> str:
    """mobile / tablet / desktop / unknown, from the browser's own description.

    Order matters: an Android tablet says "Android" without "Mobile", and every
    phone string contains something a desktop pattern would also match.
    """
    ua = user_agent or ""
    if not ua.strip():
        return "unknown"
    if _MOBILE.search(ua):
        return "mobile"
    if _TABLET.search(ua):
        return "tablet"
    if _DESKTOP.search(ua):
        return "desktop"
    return "unknown"


def client_ip(headers: dict[str, str], fallback: str | None = None) -> str | None:
    """The respondent's address as the proxy in front of us saw it.

    Behind a load balancer every request appears to come from the proxy, so the
    forwarded header is read first — its FIRST entry, which is the client; the
    rest are the hops.
    """
    forwarded = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or ""
    if forwarded.strip():
        return forwarded.split(",")[0].strip()
    real = headers.get("x-real-ip") or headers.get("X-Real-IP")
    return (real or fallback or "").strip() or None


def hashed_ip(ip: str | None, salt: str) -> str | None:
    """A stable pseudonym for one connection. Not reversible to the address."""
    if not ip:
        return None
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


def locate(ip: str | None) -> dict[str, Any]:
    """Country / region / city / postcode for an address, from the local file.

    Returns {} when there is no database, no match, or a private address — the
    caller treats that as "unknown", never as a mismatch.
    """
    path = os.getenv("DVV_GEOIP_DB", "").strip()
    if not ip or not path or not os.path.exists(path):
        return {}
    try:
        import maxminddb  # optional: only needed where a client wants the check
    except ImportError:
        return {}
    try:
        with maxminddb.open_database(path) as reader:
            record = reader.get(ip) or {}
    except (ValueError, OSError):
        return {}
    if not isinstance(record, dict):
        return {}

    def _name(part: dict | None) -> str | None:
        names = (part or {}).get("names") or {}
        return names.get("en")

    subdivisions = record.get("subdivisions") or []
    return {
        k: v
        for k, v in {
            "country": _name(record.get("country")),
            "country_code": (record.get("country") or {}).get("iso_code"),
            "region": _name(subdivisions[0] if subdivisions else None),
            "region_code": (subdivisions[0] if subdivisions else {}).get("iso_code"),
            "city": _name(record.get("city")),
            "postal": (record.get("postal") or {}).get("code"),
        }.items()
        if v
    }


def stated_place(survey: Any, answers: dict[str, Any]) -> dict[str, str]:
    """Where the respondent said they are, from their own answers.

    A postcode question first — it is the one the sample was drawn on — then a
    state/region/city question, read as the LABEL they picked rather than its
    punch code, because that is what compares to a place name.
    """
    found: dict[str, str] = {}
    for question in getattr(survey, "questions", []):
        answer = answers.get(question.qid)
        if answer in (None, "", [], {}):
            continue
        text = question.text or ""
        if "postal" not in found and _ASKS_POSTCODE.search(text):
            match = _POSTCODE.search(str(answer))
            if match:
                found["postal"] = match.group(0).upper().replace(" ", "")
        if "region" not in found and _ASKS_REGION.search(text):
            label = _label_of(question, answer)
            if label:
                found["region"] = label
    return found


def _label_of(question: Any, answer: Any) -> str | None:
    """A coded answer as its label; an open answer as itself."""
    if isinstance(answer, (list, dict)):
        return None
    for option in getattr(question, "options", None) or []:
        if str(option.code) == str(answer):
            return option.label
    return str(answer) if isinstance(answer, str) and answer.strip() else None


def place_verdict(stated: dict[str, str], geo: dict[str, Any]) -> tuple[str, str]:
    """(verdict, why) comparing what they said with where the connection is.

    Only a real disagreement counts. No database, no answer, or a country we
    cannot compare is "unknown" — a study should not flag people for running
    without a GeoIP file.
    """
    if not geo:
        return "unknown", "no location database on this server"
    if not stated:
        return "unknown", "the questionnaire does not ask where they are"

    postal, geo_postal = stated.get("postal"), str(geo.get("postal") or "").upper().replace(" ", "")
    if postal and geo_postal:
        if postal.split("-")[0] == geo_postal.split("-")[0]:
            return "match", f"postcode {postal} matches the connection"
        # A neighbouring postcode is ordinary; the same area is what matters.
        if postal[:3] == geo_postal[:3]:
            return "match", f"postcode {postal} is in the same area as {geo_postal}"
        return "mismatch", f"they typed {postal}; the connection looks like {geo_postal}"

    region = (stated.get("region") or "").strip().casefold()
    if region:
        for key in ("region", "city", "region_code", "country"):
            value = str(geo.get(key) or "").strip().casefold()
            if value and (value == region or value in region or region in value):
                return "match", f"{stated['region']} matches the connection's {key}"
        seen = geo.get("region") or geo.get("city") or geo.get("country")
        return "mismatch", f"they said {stated['region']}; the connection looks like {seen}"
    return "unknown", "nothing comparable was answered"


def summarise(entry_params: dict[str, Any]) -> dict[str, Any]:
    """The codes a data file carries for one session."""
    device = str(entry_params.get("device") or "unknown")
    place = str(entry_params.get("place_check") or "unknown")
    return {
        "device": DEVICE_CODES.get(device, 99),
        "device_label": device,
        "place": PLACE_CODES.get(place, 99),
        "place_label": place,
        "country": (entry_params.get("geo") or {}).get("country"),
        "region": (entry_params.get("geo") or {}).get("region"),
        # Looked up with the rest and then dropped, so the data file carried
        # country and region and nothing finer. Every study a client runs
        # expects city and postcode beside them — they are how a regional quota
        # is checked after the fact, and the file is the only place to check it.
        "city": (entry_params.get("geo") or {}).get("city"),
        "postal": (entry_params.get("geo") or {}).get("postal"),
        # The connection, as a salted hash. Two interviews from one connection
        # are the commonest duplicate there is, and without this column nobody
        # downstream can see them. The address itself is never stored unless a
        # client's own rules demand it (DVV_STORE_RAW_IP).
        "ip_hash": entry_params.get("ip_hash"),
    }
