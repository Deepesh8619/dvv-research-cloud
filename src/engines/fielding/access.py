"""Who may take a study's data off the platform, and from when.

Two different things are being protected, and they unlock at different moments.

The DATA LAYOUT — the data map — is settled the moment a questionnaire is
approved. Nothing about it is sensitive: it is the columns the study will
produce, and an analyst needs it to build a tab plan before field even opens.

The DATA is respondent answers. Early in field it is also misleading: the first
handful of completes are not a sample of anything, and a number taken from them
travels — into a deck, into a client call — long after the caveat is forgotten.
So it opens to the people accountable for the study from approval, and to
everyone else once the field has reached a point where the numbers mean
something: a soft launch that hit its hold, or a quota cell that filled.

The refusal always says what would change it. "Not allowed" teaches people to
ask someone with a bigger role; "available once the soft launch reaches 50
completes (currently 12)" teaches them when to come back.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Accountable for the study itself, not for one piece of analysis on it.
_ALWAYS_ALLOWED = frozenset({"owner", "founder"})


@dataclass(frozen=True)
class LinkState:
    """What one fielding link has done so far."""

    soft_launch_limit: int | None = None
    completes: int = 0

    @property
    def soft_launch_reached(self) -> bool:
        """The hold was set and field has arrived at it."""
        return self.soft_launch_limit is not None and self.completes >= self.soft_launch_limit


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def may_download_data(
    role: str,
    questionnaire_approved: bool,
    links: list[LinkState] | None = None,
    quota_filled: bool = False,
) -> Decision:
    """Whether this person may download respondent data, and why."""
    if not questionnaire_approved:
        # Before approval the instrument can still change, so any file taken now
        # describes a study that will not be the one fielded.
        return Decision(
            False,
            "The questionnaire is not approved yet, so its data layout is not "
            "final. Nobody can download data for it — including an owner.",
        )

    if role in _ALWAYS_ALLOWED:
        return Decision(True, f"{role.capitalize()}s may download an approved study's data.")

    links = links or []
    if quota_filled:
        return Decision(True, "A quota cell has filled, so the data is downloadable.")

    reached = [link for link in links if link.soft_launch_reached]
    if reached:
        return Decision(True, "The soft launch reached its hold, so the data is downloadable.")

    holds = [link for link in links if link.soft_launch_limit is not None]
    if holds:
        nearest = max(holds, key=lambda link: link.completes)
        return Decision(
            False,
            f"Available once the soft launch reaches {nearest.soft_launch_limit} "
            f"completes (currently {nearest.completes}), or once a quota cell fills. "
            f"An owner or founder can download it now.",
        )
    return Decision(
        False,
        "Available once a soft launch reaches its hold or a quota cell fills. "
        "An owner or founder can download it now.",
    )


def may_download_datamap(role: str, questionnaire_approved: bool) -> Decision:
    """The data map is the column list, not the answers: anyone who can see the
    study may have it, as soon as the questionnaire is approved."""
    if not questionnaire_approved:
        return Decision(
            False,
            "The questionnaire is not approved yet, so its columns are not final.",
        )
    return Decision(True, "An approved questionnaire's data map is available to anyone "
                          "who can see the study.")
