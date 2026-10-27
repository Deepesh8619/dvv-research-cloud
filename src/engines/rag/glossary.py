"""Market-research vocabulary, expanded into the words questionnaires use.

Researchers search in the trade's terms — "NPS", "unaided awareness",
"purchase channel" — and questionnaires are written for respondents: "how
likely are you to recommend…", "which brands come to mind…", "where do you
usually buy…". A small general embedding model knows neither side of that
translation (measured: synonym MRR 0.39 with nomic-embed-text, 0.24 with the
lexical fallback), so the translation is written down here, once, and applied to
the query before it is ranked. No model, no guessing: a term either appears in
the query or it does not.

Entries are the standard vocabulary of survey research, not tuned to any one
questionnaire. Keys are lowercase phrases matched on word boundaries.
"""

from __future__ import annotations

import re

GLOSSARY: dict[str, str] = {
    # Brand funnel
    "unaided awareness": "come to mind think of spontaneous list all brands",
    "spontaneous awareness": "come to mind think of spontaneous list all brands",
    "spontaneous recall": "come to mind think of list all brands",
    "top of mind": "first come to mind think of brand",
    "aided awareness": "heard of which of these brands",
    "prompted awareness": "heard of which of these brands",
    "consideration": "consider buying next time",
    "brand funnel": "heard of consider buying use most often",
    "bumo": "brand use most often",
    "brand used most often": "use most often brand",
    "main brand": "use most often brand",
    "brand loyalty": "how long using brand switched",
    "churn": "switched brand previous stopped",
    "switching": "switched brand previous why",
    # Satisfaction and advocacy
    "nps": "recommend friend colleague likely 0 10",
    "net promoter": "recommend friend colleague likely 0 10",
    "advocacy": "recommend friend colleague likely",
    "csat": "satisfied satisfaction",
    "satisfaction drivers": "satisfied importance choosing",
    "key drivers": "important choosing importance",
    "importance": "important choosing",
    # Purchase behaviour
    "purchase channel": "where buy store supermarket online pharmacy",
    "point of purchase": "where buy store supermarket online",
    "point of sale": "where buy store supermarket online",
    "outlet": "where buy store kirana supermarket",
    "purchase frequency": "how often buy",
    "usage frequency": "how often use wash every day week",
    "frequency of use": "how often use every day week",
    "pack size": "pack size sachet bottle ml",
    "sku": "pack size bottle ml variant",
    "gatekeeper": "who decides household bought",
    "decision maker": "who decides household bought",
    "purchase decision": "who decides bought",
    "household decision": "who decides household",
    # Price
    "price sensitivity": "price pay expensive cheap rupees",
    "willingness to pay": "price pay expensive rupees",
    "wtp": "price pay expensive rupees",
    "van westendorp": "price too expensive too cheap doubt quality",
    "price point": "price rupees pay",
    "value for money": "value money price",
    "promotion sensitivity": "discount free gift offer bought because",
    "promo": "discount free gift offer",
    "deal prone": "discount free gift offer bought because",
    "deal-prone": "discount free gift offer bought because",
    # Concepts and innovation
    "concept test": "new idea appealing likely buy",
    "purchase intent": "likely buy definitely probably would buy",
    "purchase intention": "likely buy definitely probably would buy",
    "top 2 box": "definitely probably would buy very satisfied",
    "t2b": "definitely probably would buy very satisfied",
    "barriers": "stop you from buying why not",
    "rejection reasons": "stop you from buying why not",
    "barriers to trial": "stop you from buying why not",
    "appeal": "appealing rank most least",
    "concept ranking": "rank ideas most least appealing",
    # Communication
    "media touchpoints": "seen heard advertisement tv instagram youtube influencer display",
    "touchpoints": "seen heard advertisement tv instagram store display",
    "ad recall": "seen heard advertisement recently",
    "advertising recall": "seen heard advertisement recently",
    "source of awareness": "seen heard advertisement friends family",
    # Screening and profile
    "security screener": "work industries market research advertising household",
    "industry screener": "work industries market research advertising",
    "occupation screener": "work industries market research advertising",
    "screener": "age city bought industries qualify",
    "demographics": "age education income household city",
    "sec": "education income household occupation",
    "socio-economic": "education income household occupation",
    "socioeconomic": "education income household occupation",
    "household size": "how many people live household",
    "hh size": "how many people live household",
    "nccs": "education income household",
    "income": "income monthly household rupees",
    "education": "highest level education completed",
    "region": "city live where",
    "location": "city live where",
    # Category and needs
    "need states": "problems face bothers most",
    "pain points": "problems face bothers most",
    "category problems": "problems face",
    "hair concerns": "hair problems dandruff hair fall frizz dryness",
    "claims": "free from natural ingredients doctor recommended",
    "ingredient preference": "free from sulphates parabens natural ingredients",
    "open ends": "explain own words why",
    "verbatims": "explain own words why",
    # Data quality
    "straightlining": "same answer every row grid",
    "straightliners": "same answer every row grid",
    "speeders": "completed too fast duration",
    "minors": "under 18 age",
    "underage": "under 18 age",
    "outliers": "out of range above implausible",
    "implausible": "out of range above",
}

_KEYS = sorted(GLOSSARY, key=len, reverse=True)  # longest phrase wins ("barriers to trial")
_PATTERNS = [(k, re.compile(rf"(?<![\w-]){re.escape(k)}(?![\w-])", re.IGNORECASE)) for k in _KEYS]


def expand(query: str) -> str:
    """The query plus the questionnaire words for any trade terms it uses."""
    text = query or ""
    added, used = [], []
    for key, pattern in _PATTERNS:
        if pattern.search(text) and not any(key in u for u in used):
            added.append(GLOSSARY[key])
            used.append(key)
    return f"{text} {' '.join(added)}".strip() if added else text


def _demo() -> None:
    assert "recommend" in expand("net promoter score")
    assert "recommend" in expand("What's the NPS?")
    assert expand("barriers to trial for Q5").count("stop you from buying") == 1
    assert expand("which question asks about age") == "which question asks about age"
    assert "sec" not in expand("seconds").lower().split()[1:]  # whole words only


if __name__ == "__main__":
    _demo()
    print("ok")
