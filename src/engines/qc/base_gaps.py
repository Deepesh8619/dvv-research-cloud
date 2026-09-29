"""Bases the DATA proves exist and the questionnaire never stated.

The cross-check can only verify the logic extraction found. On a real 79-question
consumer study the parser produced six `show_if` conditions — and the data file
shows three distinct answer populations, because different audiences were asked
different blocks. Everything the document said about who sees what, beyond those
six, was read by nobody and checked by nothing.

The data settles it. Among respondents who COMPLETED, a question answered by
some and not others was not skipped — it was gated. Item non-response looks
different: it is scattered, a few people per question, never the same people.
A base produces a population, and a population is exact — 403 respondents answer
P1, P2, P3, T1, T2 and T3, all six, and nobody else answers any of them.

So questions are grouped by WHO answered them, not by how many. One finding per
population rather than one per question: thirty separate "this looks gated"
lines is a wall a reviewer scrolls past, while "three audiences exist here and
the questionnaire explains one of them" is a thing somebody can act on.

Nothing is inferred about WHAT the condition is. The data shows that a gate
exists and who came through it; only the document can say why, and guessing
would put a condition in the instrument that nobody wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from src.core.survey_definition import QuestionType, QuestionVariableLink, SurveyDefinition

#: Below this share of the sample a question is not "asked of everyone", however
#: much the questionnaire implies it. Item non-response on a required question
#: is a percent or two; a base is an order of magnitude away from that.
UNIVERSAL_SHARE = 0.95
#: A population smaller than this is not worth a finding on its own — it is a
#: handful of people, and the questions it covers are reported by other checks.
MIN_POPULATION = 20


@dataclass
class Audience:
    """One set of respondents, and the questions only they answered."""

    n_respondents: int
    share: float
    qids: list[str] = field(default_factory=list)
    #: Of those questions, the ones the questionnaire gives no base for.
    unexplained_qids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_respondents": self.n_respondents,
            "share": round(self.share, 3),
            "qids": list(self.qids),
            "unexplained_qids": list(self.unexplained_qids),
        }


def find_audiences(
    survey: SurveyDefinition,
    links: list[QuestionVariableLink],
    parquet_path: Path,
    columns: set[str] | None = None,
) -> list[Audience]:
    """Answer populations in the data, largest first. Empty when everyone saw
    everything, which is what a questionnaire with no routing should produce."""
    by_qid = {q.qid: q for q in survey.questions}
    if not Path(parquet_path).is_file():
        # The free tier wipes the disk; a study can outlive its own data file.
        # Nothing to look at is not the same as nothing found, and the caller
        # already reports `checked` separately for exactly that reason.
        return []
    con = duckdb.connect()
    try:
        con.execute("SET memory_limit = '512MB'")
        con.execute("SET threads = 2")
        # A stable row number, because a parquet view has no rowid and the
        # checksum below has to identify WHICH respondents answered, not just
        # how many: two questions with 403 answers each are one audience only
        # if they are the same 403.
        con.execute(
            "CREATE VIEW d AS SELECT *, row_number() OVER () AS __row "
            f"FROM read_parquet('{parquet_path}')"
        )
        available = columns or {
            row[1] for row in con.execute("PRAGMA table_info('d')").fetchall()
        }
        total = con.execute("SELECT count(*) FROM d").fetchone()[0]
        if not total:
            return []

        # Group by WHO answered, not by how many: two questions with 403
        # respondents each are one audience only if they are the SAME 403.
        # A checksum over the answering rows identifies the set without
        # holding it.
        populations: dict[tuple[int, int], list[str]] = {}
        for link in links:
            question = by_qid.get(link.qid)
            if question is None or question.type is QuestionType.INFO:
                continue
            cols = [f'"{v}"' for v in link.variable_names if v in available]
            if not cols:
                continue
            answered = " OR ".join(f"{c} IS NOT NULL" for c in cols)
            row = con.execute(
                f"SELECT count(*), coalesce(sum(__row), 0) FROM d WHERE {answered}"
            ).fetchone()
            n, checksum = int(row[0]), int(row[1])
            if n >= total * UNIVERSAL_SHARE or n < MIN_POPULATION:
                continue
            populations.setdefault((n, checksum), []).append(link.qid)
    finally:
        con.close()

    audiences = [
        Audience(
            n_respondents=n,
            share=n / total,
            qids=sorted(qids),
            unexplained_qids=sorted(
                qid for qid in qids
                if by_qid[qid].show_if is None and by_qid[qid].terminate_if is None
            ),
        )
        for (n, _checksum), qids in populations.items()
    ]
    return sorted(audiences, key=lambda a: (-a.n_respondents, a.qids[0]))


def summarise(audiences: list[Audience]) -> dict[str, Any]:
    """What to tell a reviewer, in the shape the crosscheck response uses."""
    unexplained = [a for a in audiences if a.unexplained_qids]
    return {
        "n_audiences": len(audiences),
        "n_unexplained": len(unexplained),
        "audiences": [a.to_dict() for a in audiences],
        "detail": (
            "; ".join(
                f"{a.n_respondents} respondents answered "
                f"{', '.join(a.unexplained_qids[:6])}"
                + (" and others" if len(a.unexplained_qids) > 6 else "")
                for a in unexplained
            )
            or ""
        ),
    }
