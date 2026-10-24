"""Key drivers are offered rating scales only — not gender, age bands or income."""

import pytest

from api.routes.methods import _is_scale


@pytest.mark.parametrize("values,expected", [
    ({1: "Male", 2: "Female", 3: "Non-binary", 4: "Prefer not to say"}, False),
    ({1: "13-17", 2: "18-25", 3: "26-34", 4: "35+"}, False),
    ({1: "Less than $10,000", 2: "$10,000 - $49,999", 3: "$50,000 - $99,999",
      4: "$100,000+"}, False),
    ({1: "1 - Strongly Disagree", 2: "2", 3: "3", 4: "4", 5: "5 - Strongly agree"}, True),
    ({1: "Very dissatisfied", 2: "Somewhat dissatisfied", 3: "Neither", 4: "Somewhat satisfied",
      5: "Very satisfied"}, True),
    ({i: str(i) for i in range(11)}, True),
])
def test_scales(values, expected):
    assert _is_scale(values) is expected
