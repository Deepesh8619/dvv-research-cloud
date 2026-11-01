"""Note sentences and band splitting, on made-up lines."""

from src.engines.questionnaire.note_lines import sentences, split_groups


def test_run_together_sentences_split_at_the_stop():
    got = sentences(["Set the limit.Terminate if the answer is over 5."])
    assert got == ["Set the limit.", "Terminate if the answer is over 5."]


def test_numbers_and_short_codes_are_not_split():
    line = "Use Q.4 then S1.a and the 1.5 step, e.g.Keep this one. Done."
    got = sentences([line])
    assert "Use Q.4 then S1.a and the 1.5 step, e.g.Keep this one." in got
    assert got[-1] == "Done."


def test_spaced_sentences_still_split():
    assert sentences(["Ask all. Keep order."]) == ["Ask all.", "Keep order."]


def test_a_second_create_variable_on_one_line_starts_a_new_note():
    got = sentences(["Create variable AGE: 18-34 Create variable SEX: M, F."])
    assert got == ["Create variable AGE: 18-34", "Create variable SEX: M, F."]


def test_a_single_create_variable_is_left_alone():
    line = "Create variable AGE: 18-34, 35-49."
    assert sentences([line]) == [line]


def test_run_together_age_bands_split():
    assert split_groups("18-3435-4950-6465+") == ["18-34", "35-49", "50-64", "65+"]


def test_run_together_gender_and_age_bands_split():
    assert split_groups("M18-34F18-34M65+F65+") == ["M18-34", "F18-34", "M65+", "F65+"]


def test_a_band_that_does_not_rise_is_not_split():
    assert split_groups("50-3065+") == ["50-3065+"]


def test_implausible_ages_come_back_whole():
    assert split_groups("5-9") == ["5-9"]
    assert split_groups("18-34130-140") == ["18-34130-140"]


def test_one_band_and_empty_text_come_back_whole():
    assert split_groups("18-34") == ["18-34"]
    assert split_groups("") == [""]


def test_unparseable_text_comes_back_whole():
    assert split_groups("young adults or older people") == ["young adults or older people"]
