"""Who can do what.

Written because `founder` was added for the correction loop and never added to
the ~21 endpoints already gated on ANALYST — so Vaibhav and Vishal could teach
the parser but could not create a project. A role that can do the advanced
thing and not the basic one is a nonsense state, and it reached production.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from api.auth import UserRole, require_role


class _User:
    def __init__(self, role: str) -> None:
        self.role = role


def _check(dep, role: str):
    """Run the dependency's inner check with a user of `role`.

    asyncio.run rather than pytest-asyncio: four tests do not justify a new
    test dependency in the install path.
    """
    return asyncio.run(dep(user=_User(role)))


def test_founder_can_do_anything_an_analyst_can():
    """The bug: founders were refused on every ANALYST-gated endpoint."""
    dep = require_role(UserRole.OWNER, UserRole.ANALYST)
    assert (_check(dep, "founder")).role == "founder"


def test_owner_and_analyst_still_pass():
    dep = require_role(UserRole.OWNER, UserRole.ANALYST)
    assert (_check(dep, "owner")).role == "owner"
    assert (_check(dep, "analyst")).role == "analyst"


def test_reviewer_is_still_refused():
    """Widening founder must not widen everyone."""
    dep = require_role(UserRole.OWNER, UserRole.ANALYST)
    with pytest.raises(HTTPException) as exc:
        _check(dep, "reviewer")
    assert exc.value.status_code == 403


def test_owner_only_endpoints_stay_owner_only():
    """A founder is NOT an owner — user admin and global rules stay restricted."""
    dep = require_role(UserRole.OWNER)
    with pytest.raises(HTTPException) as exc:
        _check(dep, "founder")
    assert exc.value.status_code == 403
