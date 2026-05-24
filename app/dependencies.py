"""
Shared FastAPI Depends() helpers.
All routers import from here — never import directly from auth.service.
"""
from app.auth.service import get_current_user
from app.auth.schemas import AuthUser

__all__ = ["get_current_user", "AuthUser"]
