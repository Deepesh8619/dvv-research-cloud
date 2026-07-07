"""Questionnaire extraction engine.

Turns the deterministic `QuestionChunk`s produced by
`src.intake.questionnaire_loader` into structured `Question`/`SurveyDefinition`
objects using a schema-constrained LLM extraction pass, a deterministic
per-field validation pass, and a second grounded LLM verification pass.

This is the HIGHEST-RISK step in the project: it is the only place where free
prose becomes structured survey logic, so every LLM output is treated as a
suspect that must survive validation and verification before it is trusted.
"""

from __future__ import annotations

from src.engines.questionnaire.extractor import (
    ExtractionResult,
    extract_question,
    extract_survey_definition,
    verify_question,
)
from src.engines.questionnaire.schema import (
    EXTRACTION_SCHEMA,
    VERIFICATION_SCHEMA,
    build_extraction_prompt,
    build_verification_prompt,
)

__all__ = [
    "EXTRACTION_SCHEMA",
    "VERIFICATION_SCHEMA",
    "ExtractionResult",
    "build_extraction_prompt",
    "build_verification_prompt",
    "extract_question",
    "extract_survey_definition",
    "verify_question",
]
