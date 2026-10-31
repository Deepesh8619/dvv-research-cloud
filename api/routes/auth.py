"""Authentication endpoints."""

import hmac
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from api.auth import (
    UserRole,
    create_access_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from api.config import ADMIN_EMAIL, ADMIN_RESET_TOKEN, JWT_ALGORITHM, JWT_EXPIRY_HOURS, JWT_SECRET
from api.db import (
    UserDB,
    create_user,
    get_db,
    get_user_by_email,
    list_users,
    record_audit,
)
from api.models import (
    CreateUserRequest,
    EmergencyResetRequest,
    LoginResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Sign-in throttling --------------------------------------------------------
# An internet-facing login with no limit lets anyone try passwords forever. Two
# keys: the ACCOUNT (guessing one person's password) and the ADDRESS (spraying
# many accounts from one machine). Past the threshold the key is locked for 1
# minute, doubling per further failure up to 15; a success clears the account.
# The lock is checked BEFORE the password, so a locked account can't be probed.
# ponytail: in-memory, one web process per client server (api/heavy.py); move
# to a DB table if several web processes ever serve one database.
_WINDOW = 15 * 60
_MAX_LOCK = 15 * 60
_THRESHOLDS = {"account": 5, "address": 30}
_failures: dict[str, list[float]] = {}
_throttle_lock = threading.Lock()


def _locked_for(key: str, kind: str, now: float) -> int:
    """Seconds this key must still wait (0 = allowed)."""
    with _throttle_lock:
        recent = [t for t in _failures.get(key, []) if now - t < _WINDOW]
        _failures[key] = recent
        over = len(recent) - _THRESHOLDS[kind]
        if over < 0:
            return 0
        wait = min(60 * 2**over, _MAX_LOCK) - (now - recent[-1])
        return max(0, int(wait) + 1)


def _record_failure(*keys: str) -> None:
    now = time.monotonic()
    with _throttle_lock:
        for key in keys:
            _failures.setdefault(key, []).append(now)


@router.post("/auth/login", response_model=LoginResponse)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Log in with email and password, return JWT token."""
    account = f"account:{form_data.username.strip().lower()}"
    address = f"address:{request.client.host if request.client else 'unknown'}"
    now = time.monotonic()
    wait = max(_locked_for(account, "account", now), _locked_for(address, "address", now))
    if wait:
        minutes = max(1, round(wait / 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed sign-ins. Try again in {minutes} minute"
            f"{'s' if minutes != 1 else ''}.",
            headers={"Retry-After": str(wait)},
        )

    user = get_user_by_email(db, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        _record_failure(account, address)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    with _throttle_lock:
        _failures.pop(account, None)  # a correct password clears the account's count

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    token = create_access_token(
        data={"sub": user.email},
        secret=JWT_SECRET,
        algorithm=JWT_ALGORITHM,
        expires_in_hours=JWT_EXPIRY_HOURS,
    )

    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            created_at=user.created_at,
            is_active=user.is_active,
        ),
    )


@router.post("/auth/emergency-reset")
def emergency_reset(
    request: EmergencyResetRequest,
    db: Session = Depends(get_db),
):
    """Reset the admin account's password given ADMIN_RESET_TOKEN.

    Deliberately unauthenticated — this exists to recover a locked-out owner
    account (e.g. seed_admin_user's env-var sync hasn't landed yet, or the
    wrong value got saved), so it can't require already being logged in.
    Two things bound the blast radius instead: ADMIN_RESET_TOKEN must be
    explicitly set (empty by default -> 404, not a standing backdoor), and
    only ADMIN_EMAIL's account can be reset this way, never an arbitrary
    email — an attacker who guessed the token still can't take over other
    users' accounts.
    """
    if not ADMIN_RESET_TOKEN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not hmac.compare_digest(request.token, ADMIN_RESET_TOKEN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid reset token")

    if request.email != ADMIN_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the configured admin account can be reset this way",
        )

    user = get_user_by_email(db, request.email)
    if user:
        user.password_hash = hash_password(request.new_password)
        # This is a deliberate, manual password change. Mark it so the next
        # cold-start seed_admin_user() does NOT re-sync it back to ADMIN_PASSWORD
        # — the exact bug where a just-set password reverted after the Render
        # container idled and restarted.
        user.password_synced_from_env = False
        db.commit()
        logger.warning(f"Admin password reset via emergency-reset endpoint: {request.email}")
    else:
        create_user(
            db,
            email=request.email,
            name="Admin",
            password_hash=hash_password(request.new_password),
            role="owner",
            # Freshly created via an explicit reset — also a deliberate password,
            # not an env-var sync target, so keep it from being overwritten.
            password_synced_from_env=False,
        )
        logger.warning(f"Admin user created via emergency-reset endpoint: {request.email}")

    return {"status": "ok", "message": "Password reset — you can log in now"}


@router.get("/auth/me", response_model=UserResponse)
def get_me(user: UserDB = Depends(get_current_user)):
    """Get current authenticated user."""
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        created_at=user.created_at,
        is_active=user.is_active,
    )


@router.get("/auth/users", response_model=list[UserResponse])
def list_all_users(
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    """List all users (Owner-only)."""
    users = list_users(db)
    return [
        UserResponse(
            id=u.id,
            email=u.email,
            name=u.name,
            role=u.role,
            created_at=u.created_at,
            is_active=u.is_active,
        )
        for u in users
    ]


@router.post("/auth/users", response_model=UserResponse)
def create_new_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    """Create a new user (Owner-only)."""
    # Check if user already exists
    existing = get_user_by_email(db, request.email)
    if existing and not existing.is_active:
        # Someone removed earlier and added back: the same account, so their
        # name on past work stays theirs. The new password and role apply.
        try:
            UserRole(request.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid role") from exc
        existing.name, existing.role = request.name, request.role
        existing.password_hash = hash_password(request.password)
        existing.is_active = True
        record_audit(db, entity="user", entity_id=existing.id, action="restored",
                     actor_id=user.id, summary=f"Added back {existing.email}")
        db.commit()
        return UserResponse(id=existing.id, email=existing.email, name=existing.name,
                            role=existing.role, created_at=existing.created_at,
                            is_active=True)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )

    # Validate role
    try:
        UserRole(request.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join([r.value for r in UserRole])}",
        ) from exc

    # Create user
    new_user = create_user(
        db,
        email=request.email,
        name=request.name,
        password_hash=hash_password(request.password),
        role=request.role,
    )

    logger.info(f"Created user: {new_user.email} with role {new_user.role}")

    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        name=new_user.name,
        role=new_user.role,
        created_at=new_user.created_at,
        is_active=new_user.is_active,
    )


@router.delete("/auth/users/{user_id}", status_code=204)
def remove_user(
    user_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    """Remove a user's access (Owner-only). The account is deactivated, not erased:
    their name stays on what they approved and changed, and every token they hold
    stops working at once. Adding the same email again restores it."""
    target = db.get(UserDB, user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="No such user")
    if target.id == user.id:
        raise HTTPException(status_code=409, detail="You cannot remove yourself.")
    if target.role == UserRole.OWNER.value and sum(
            1 for u in list_users(db) if u.role == UserRole.OWNER.value) <= 1:
        raise HTTPException(status_code=409, detail="This is the last owner.")
    target.is_active = False
    record_audit(db, entity="user", entity_id=target.id, action="removed",
                 actor_id=user.id, summary=f"Removed {target.email}")
    db.commit()
    logger.info(f"User {target.email} removed by {user.email}")
