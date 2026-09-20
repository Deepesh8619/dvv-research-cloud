"""What the interview says about a respondent, beside their answers."""

import pytest

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.paradata import (
    client_ip,
    device_of,
    hashed_ip,
    locate,
    place_verdict,
    stated_place,
    summarise,
)

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1 Mobile/15E148"
GALAXY_TAB = "Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 Chrome/120 Safari/537.36"
PIXEL = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
)
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (IPHONE, "mobile"), (PIXEL, "mobile"), (GALAXY_TAB, "tablet"),
        (MAC, "desktop"), ("", "unknown"),
    ],
)
def test_the_device_is_read_from_the_browsers_own_description(agent, expected):
    """An Android TABLET says "Android" without "Mobile" — read the other way
    round, every tablet in the sample is counted as a phone."""
    assert device_of(agent) == expected


def test_the_address_is_the_client_not_the_proxy():
    """Behind a load balancer every request appears to come from the proxy."""
    assert client_ip({"x-forwarded-for": "203.0.113.9, 10.0.0.1"}) == "203.0.113.9"
    assert client_ip({}, fallback="198.51.100.4") == "198.51.100.4"
    assert client_ip({}) is None


def test_the_address_itself_is_not_kept():
    """A salted hash recognises one connection twice without storing where
    anybody lives, and cannot be read back into an address."""
    one = hashed_ip("203.0.113.9", "salt")
    assert one == hashed_ip("203.0.113.9", "salt")
    assert one != hashed_ip("203.0.113.10", "salt")
    assert "203.0.113.9" not in one
    assert hashed_ip(None, "salt") is None


def test_without_a_location_database_nothing_is_looked_up_anywhere():
    """No respondent's address is sent to a third-party service — with no local
    database the location is simply unknown."""
    assert locate("203.0.113.9") == {}


def _survey() -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[],
        questions=[
            Question(qid="S1", text="What is your ZIP code?", type=QuestionType.OPEN_TEXT),
            Question(qid="S2", text="Which state do you live in?",
                     options=[Option(code=1, label="New York"), Option(code=2, label="Texas")]),
        ],
    )


def test_what_the_respondent_said_about_where_they_are():
    place = stated_place(_survey(), {"S1": "11042", "S2": 1})
    assert place == {"postal": "11042", "region": "New York"}


def test_a_neighbouring_postcode_is_not_a_mismatch():
    """The sample was drawn on an AREA. Flagging a respondent because their
    connection resolves one postcode over would throw away real interviews."""
    assert place_verdict({"postal": "11042"}, {"postal": "11042"})[0] == "match"
    assert place_verdict({"postal": "11042"}, {"postal": "11049"})[0] == "match"
    assert place_verdict({"postal": "11042"}, {"postal": "90210"})[0] == "mismatch"


def test_nothing_to_compare_is_never_a_mismatch():
    assert place_verdict({"postal": "11042"}, {})[0] == "unknown"
    assert place_verdict({}, {"postal": "11042"})[0] == "unknown"
    verdict, why = place_verdict({"region": "Queens"}, {"city": "Queens", "country": "US"})
    assert verdict == "match" and "Queens" in why


def test_the_codes_a_data_file_carries():
    assert summarise({"device": "mobile", "place_check": "mismatch"})["device"] == 1
    assert summarise({"device": "mobile", "place_check": "mismatch"})["place"] == 2
    assert summarise({})["device"] == 99 and summarise({})["place"] == 99
