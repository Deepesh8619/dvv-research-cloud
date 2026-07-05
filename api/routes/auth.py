"""Authentication endpoints."""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.auth import (
    UserRole,
    create_access_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from api.config import JWT_ALGORITHM, JWT_EXPIRY_HOURS, JWT_SECRET
from api.db import (
    UserDB,
    create_user,
    get_db,
    get_user_by_email,
    list_users,
)
from api.models import (
    CreateUserRequest,
    LoginResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/auth/login", response_model=LoginResponse)
async def login(
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


@router.get("/auth/me", response_model=UserResponse)
async def get_me(user: UserDB = Depends(get_current_user)):
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
async def list_all_users(
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
async def create_new_user(
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
