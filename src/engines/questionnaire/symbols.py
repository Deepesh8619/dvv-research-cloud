"""A small picture for a question, read from the question's own words.

A survey is a column of grey text on a phone, and by question forty nobody is
reading carefully any more. One symbol at the top of each screen gives the
respondent something to land on: it says "this one is about the pharmacy you
use" before they have read a word, and it changes as the subject changes, so
the interview visibly moves instead of repeating.

Rules this follows, learned from the rest of the extraction code:

* **Deterministic and literal.** Substring matching against the wording that is
  already in the questionnaire. No model call, no network, no cost, and the
  same question always gets the same symbol.
* **Subject before type.** "How often do you visit a pharmacy?" is a pharmacy
  question, not a frequency question — the topic is what a respondent
  recognises. The question's TYPE is only the fallback when nothing in the
  wording is recognisable.
* **A wrong picture is worse than none.** Matches are on whole words, so
  "medicare" does not become "care" and "brandy" does not become "brand".

The symbol names are drawn by the front end as line marks; nothing is fetched,
so a survey on a bad connection looks the same as one on fibre.
"""

from __future__ import annotations

import re
from typing import Any

#: Topic -> the words that mean it. Ordered: the first topic that matches wins,
#: so the specific ones (pharmacy, insurance) sit above the general ones
#: (shopping, money).
TOPICS: list[tuple[str, tuple[str, ...]]] = [
    ("pharmacy", ("pharmacy", "pharmacies", "pharmacist", "prescription", "prescriptions",
                  "chemist", "drugstore", "refill", "refills", "medication", "medicines")),
    ("health", ("health", "healthcare", "doctor", "doctors", "clinic", "hospital", "symptom",
                "symptoms", "diagnosis", "treatment", "illness", "wellbeing", "wellness")),
    ("insurance", ("insurance", "insurer", "policy", "premium", "premiums", "claim", "claims",
                   "cover", "coverage", "deductible")),
    ("banking", ("bank", "banking", "account", "loan", "loans", "credit card", "debit card",
                 "savings", "investment", "investments", "mutual fund", "emi")),
    ("beauty", ("skincare", "skin", "moisturiser", "moisturizer", "cleanser", "serum",
                "sunscreen", "makeup", "cosmetic", "cosmetics", "shampoo", "fragrance",
                "lotion", "lip", "hair")),
    ("food", ("food", "snack", "snacks", "meal", "meals", "breakfast", "lunch", "dinner",
              "grocery", "groceries", "cooking", "recipe", "restaurant", "biscuit", "chocolate")),
    ("drink", ("drink", "drinks", "beverage", "beverages", "coffee", "tea", "juice", "soda",
               "water", "beer", "wine", "alcohol", "soft drink")),
    ("vehicle", ("car", "cars", "vehicle", "vehicles", "bike", "motorcycle", "scooter",
                 "driving", "drive", "fuel", "petrol", "diesel", "dealership")),
    ("travel", ("travel", "flight", "flights", "airline", "hotel", "holiday", "vacation",
                "trip", "booking", "airport", "tourism")),
    ("device", ("phone", "smartphone", "mobile", "laptop", "tablet", "computer", "device",
                "app", "apps", "website", "online", "internet", "software", "streaming")),
    ("shopping", ("shop", "shopping", "store", "stores", "retailer", "retail", "purchase",
                  "purchased", "buy", "bought", "basket", "supermarket", "checkout")),
    ("money", ("price", "prices", "pricing", "cost", "costs", "spend", "spending", "budget",
               "expensive", "cheap", "afford", "value for money", "pay", "paid", "discount")),
    ("recommend", ("recommend", "recommendation", "friend or colleague", "tell others",
                   "net promoter", "likely are you to suggest")),
    ("brand", ("brand", "brands", "heard of", "awareness", "aware of", "logo", "advertised")),
    ("media", ("advertisement", "advertising", "advert", "ad", "ads", "campaign", "commercial",
               "social media", "instagram", "youtube", "facebook", "news")),
    ("work", ("work", "working", "job", "jobs", "employment", "employer", "occupation",
              "profession", "career", "office", "colleague", "colleagues")),
    ("household", ("household", "family", "home", "house", "living with", "spouse",
                   "partner", "married", "housemate")),
    ("child", ("child", "children", "kids", "baby", "babies", "toddler", "parent", "parenting",
               "school", "student", "education")),
    ("pet", ("pet", "pets", "dog", "dogs", "cat", "cats", "puppy", "kitten")),
    ("place", ("zip", "zip code", "postal", "postcode", "state", "province", "region", "city",
               "county", "where do you live", "located", "location", "area")),
    ("person", ("age", "gender", "male", "female", "you describe yourself", "your date of birth",
                "how old", "ethnicity", "income")),
    ("time", ("how often", "frequency", "times per", "times a", "last 6 months", "last month",
              "last week", "past year", "regularly", "daily", "weekly", "monthly")),
    ("star", ("satisfied", "satisfaction", "rate", "rating", "how would you rate", "opinion of",
              "impression", "experience with", "likelihood")),
    ("agree", ("agree", "disagree", "statement", "statements", "how much do you agree")),
]

#: Only when nothing in the wording is recognisable. Never wrong, never
#: interesting — the question's shape, drawn as a mark.
BY_TYPE: dict[str, str] = {
    "single": "list",
    "multi": "checklist",
    "dropdown": "list",
    "grid_single": "grid",
    "numeric": "number",
    "rank": "rank",
    "open_text": "comment",
}

#: Every name the front end can draw. A name outside this set is a bug, not a
#: blank space, so the test suite asserts the whole table stays inside it.
KNOWN = frozenset(
    [topic for topic, _ in TOPICS] + list(BY_TYPE.values()) + ["share"]
)

_WORD_CACHE: dict[str, re.Pattern[str]] = {}


def _word(term: str) -> re.Pattern[str]:
    """Whole-word (or whole-phrase) matcher for one term.

    Substring matching alone turned "medicare" into care and "brandy" into
    brand. The boundaries are what make a literal match safe to trust.
    """
    if term not in _WORD_CACHE:
        _WORD_CACHE[term] = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.I)
    return _WORD_CACHE[term]


def symbol_for(question: Any) -> str:
    """The symbol name for one question. Always returns something drawable.

    The WORDING is read first and the answer labels only after it. "Do you or
    anyone in your household work in any of these industries?" listed
    "IT / software" among its answers and came back as a question about
    devices — the answers describe what can be picked, the question says what
    is being asked.
    """
    asked = getattr(question, "text", "") or ""
    answers = " ".join(
        [" ".join(o.label or "" for o in (getattr(question, "options", None) or [])[:12]),
         " ".join(r.label or "" for r in (getattr(question, "rows", None) or [])[:12])]
    )
    for haystack in (asked, answers):
        if not haystack.strip():
            continue
        for topic, terms in TOPICS:
            if any(_word(term).search(haystack) for term in terms):
                return topic

    qtype = str(getattr(getattr(question, "type", ""), "value", getattr(question, "type", "")))
    # Numbers that must add up are their own thing: a respondent is splitting
    # something, not counting it.
    if qtype == "numeric" and (getattr(question, "validation", None) or {}).get("sum"):
        return "share"
    return BY_TYPE.get(qtype, "list")


def symbols_for_survey(survey: Any) -> dict[str, str]:
    """{qid: symbol} for every question a respondent will actually see."""
    return {
        q.qid: symbol_for(q)
        for q in getattr(survey, "questions", []) or []
        if str(getattr(q.type, "value", q.type)) != "info"
    }
