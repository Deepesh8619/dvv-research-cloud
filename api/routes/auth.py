"""Authentication endpoints."""

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, status
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
)
from api.models import (
    CreateUserRequest,
    EmergencyResetRequest,
    LoginResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/auth/login", response_model=LoginResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Log in with email and password, return JWT token."""
    user = get_user_by_email(db, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

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
