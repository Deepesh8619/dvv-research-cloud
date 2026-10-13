"""PII masking for delivered respondent files and the live view.

Raw data is never touched; callers mask a COPY on the way out.

Measured: 0 hits on the 3 real client files (no false positives there).
Recall is UNMEASURED - names/labels are matched by a short conservative list
and text by email/phone patterns only. Free-text names and street addresses
in open-ends are NOT detected. Treat this as a safety net, not a guarantee.
"""

import re

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
# 10-13 digits with optional space/hyphen/dot separators and optional +; this
# covers Indian mobiles (+91 optional, [6-9]\d{9}) as a subset.
_PHONE = re.compile(r"(?<![\w.])\+?\d(?:[\s.-]?\d){9,12}(?![\w])")

_NAME_TOKENS = {
    "email", "emailaddress", "phone", "mobile", "telephone", "fname", "lname",
    "firstname", "lastname", "fullname", "surname", "address", "ipaddress", "rawip",
}
_LABEL = re.compile(
    r"\be-?mail\s*(?:address|id)\b"
    r"|^\s*(?:your\s+)?e-?mail\s*:?\s*$"
    r"|\b(?:phone|mobile|cell(?:ular)?|contact|telephone|tel)\s*(?:number|no\.?|#)"
    r"|\btelephone\b"
    r"|\b(?:full|first|last|sur|family|your|respondent'?s?)\s*name\b"
    r"|^\s*name\s*:?\s*$"
    r"|\b(?:home|street|mailing|residential|postal|ip|your|full|email)\s*address\b"
    r"|^\s*address\s*:?\s*$"
    r"|^\s*(?:phone|mobile)\s*:?\s*$",
    re.I,
)


def is_pii_label(name: str, label: str = "") -> bool:
    if any(t in _NAME_TOKENS for t in re.split(r"[^a-z0-9]+", (name or "").lower())):
        return True
    return bool(_LABEL.search(label or ""))


def pii_columns(datamap) -> list[str]:
    return [v.name for v in datamap.variables if is_pii_label(v.name, v.label)]


def mask_text(s):
    if not isinstance(s, str):
        return s
    s = _EMAIL.sub("***@" + r"\1", s)
    return _PHONE.sub("***", s)
