"""JWT authentication, password hashing, and role management."""

import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from api.db import get_db, get_user_by_email

logger = logging.getLogger(__name__)

# Hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class UserRole(StrEnum):
    """User roles with permissions."""

    OWNER = "owner"
    # Founders' lane (correction loop): may write corrections against an
    # extracted questionnaire and approve profile-scoped rules. Deliberately
    # NOT a superset of ANALYST — it grants the correction surface only, and
    # `global`-scope rules still require OWNER (see routes/corrections.py).
    FOUNDER = "founder"
    ANALYST = "analyst"
    REVIEWER = "reviewer"


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain, hashed)


def create_access_token(
    data: dict,
    secret: str,
    algorithm: str,
    expires_in_hours: int,
) -> str:
    """Create a JWT token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=expires_in_hours)
    to_encode.update({"exp": expire})
    encoded = jwt.encode(to_encode, secret, algorithm=algorithm)
    return encoded


def decode_access_token(token: str, secret: str, algorithm: str) -> dict | None:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Session = Depends(get_db),
):
    """Dependency to get the current authenticated user from JWT token."""
    from api.config import JWT_ALGORITHM, JWT_SECRET

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token, JWT_SECRET, JWT_ALGORITHM)
    if payload is None:
        raise credentials_exception

    email = payload.get("sub")
    if email is None:
        raise credentials_exception

    user = get_user_by_email(db, email)
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    return user


def role_permits(user, *allowed_roles: UserRole) -> bool:
    """The rule require_role enforces, for code that is not an endpoint (the
    Copilot reading a screen on the user's behalf)."""
    role = UserRole(user.role)
    return role in allowed_roles or (
        role is UserRole.FOUNDER and UserRole.ANALYST in allowed_roles
    )


def require_role(*allowed_roles: UserRole):
    """Dependency factory to enforce role-based access.

    FOUNDER satisfies anything ANALYST can do. Founders are the research leads
    — they run studies as well as correct the parser, so a role that could
    teach the extractor but not create a project was a nonsense state (and was
    exactly the bug: `founder` was added for the correction loop and never
    added to the ~21 endpoints already gated on ANALYST).

    Implemented here rather than by editing every call site, so the two can
    never drift apart. Anything gated on OWNER alone stays owner-only.
    """

    async def check_role(user=Depends(get_current_user)):
        if not role_permits(user, *allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(allowed_roles)}",
            )
        return user

    return check_role
