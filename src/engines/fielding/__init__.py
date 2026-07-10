"""Fielding engine: turn captured respondent sessions into analysis artifacts.

T11 bridges the two halves of the model (key decision #1): `SurveyDefinition`
describes the *instrument*, `DataMap` describes *collected data*. This package
derives the second from the first plus real respondent sessions, so the whole
existing analysis stack (crosstabs, navigator, QC, coding) runs on live fielded
data with zero changes to those engines.
"""

from src.engines.fielding.materialize import materialize_fielding

__all__ = ["materialize_fielding"]
