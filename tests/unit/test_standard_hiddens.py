"""The hidden variables every study carries, and the ones a note declares.

Two different things travel under the same word. The STANDARD hiddens are
system facts about the interview — connection, device, place, the clock — and
belong to every study whatever it asks. The DECLARED ones are named by the
document, and the only reliable signal is that it says so: "AGE" is a question
in one study and a hidden recode in the next, and the name never tells you.
"""

from datetime import datetime

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.materialize import STATUS_COMPLETE, materialize_fielding
from src.engines.questionnaire.prose_directives import parse_prose_question


class _Session:
    def __init__(self, sid, entry_params):
        self.id = sid
        self.status_code = STATUS_COMPLETE
        self.started_at = datetime(2026, 1, 1, 9, 0).isoformat()
        self.completed_at = datetime(2026, 1, 1, 9, 12).isoformat()
        self.last_seen_at = None
        self.current_page = 1
        self.pid = "p1"
        self.block_order = None
        self.maxdiff_version = None
        self.entry_params = entry_params


def test_the_standard_hiddens_all_reach_the_data_file():
    """Country and region were carried and city, postcode and the connection
    were not — all four looked up together and three of them dropped.

    A regional quota is checked after the fact from the file, and two
    interviews from one connection are the commonest duplicate there is.
    """
    asked = Question(
        qid="S1", text="Age?", type=QuestionType.SINGLE,
        options=[Option(code=1, label="18-34")],
    )
    survey = SurveyDefinition(
        study_id="t", blocks=[Block(block_id="b", question_ids=["S1"])],
        questions=[asked],
    )
    session = _Session("r1", {
        "device": "mobile", "ip_hash": "a1b2c3", "place_check": "match",
        "geo": {"country": "United States", "region": "New York",
                "city": "Queens", "postal": "11375"},
    })
    datamap, frame = materialize_fielding(survey, [session], {"r1": {"S1": 1}})
    names = {v.name for v in datamap.variables}
    assert {
        "respondent_id", "vStartTime", "vEndTime", "vDurationSec", "vDevice",
        "vCountry", "vRegion", "vCity", "vPostcode", "vConnection",
    } <= names
    row = frame.iloc[0]
    assert row["vCity"] == "Queens"
    assert row["vPostcode"] == "11375"
    assert row["vConnection"] == "a1b2c3"


def test_the_connection_is_a_hash_never_an_address():
    """The column exists so duplicates can be seen. It is not an address, and
    the raw one is kept only where a client's own rules demand it."""
    from src.engines.fielding.paradata import hashed_ip

    assert hashed_ip("203.0.113.9", "salt") != "203.0.113.9"
    assert hashed_ip(None, "salt") is None


def test_a_note_that_says_hidden_outranks_the_wording():
    """"AGE" is a question in one study and a hidden recode in the next. The
    note beside it is the only thing that tells them apart, and it was not
    being read at all on the prose path."""
    fields = parse_prose_question(
        "S3a. AGE\n[PN: HIDDEN VARIABLE. COMPUTE FROM S3]\n1. 18-34\n2. 35+", "S3a"
    )
    assert fields.derived is True
    assert fields.qtype is QuestionType.INFO
    assert "HIDDEN VARIABLE" in fields.evidence["derived"]


def test_the_same_wording_without_the_note_stays_a_question():
    fields = parse_prose_question("S3. What is your age?\n1. 18-34\n2. 35+", "S3")
    assert fields.derived is False
    assert fields.qtype is QuestionType.SINGLE


def test_hidden_in_the_respondent_s_own_wording_is_not_a_declaration():
    """Only a DIRECTIVE declares it. A question that uses the word is a
    question — the note is what speaks for the scripter."""
    fields = parse_prose_question(
        "Q5. Which of these have you hidden from view?\n1. Yes\n2. No", "Q5"
    )
    assert fields.derived is False
