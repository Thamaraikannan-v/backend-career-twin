from fastapi import APIRouter, Depends
from app.auth.schemas import AuthUser
from app.dependencies import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=AuthUser)
async def me(user: AuthUser = Depends(get_current_user)):
    """
    Returns the currently authenticated user.
    Useful for the frontend to validate the session on mount.
    """
    return user
