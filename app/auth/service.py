from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from app.config import get_settings
from app.auth.schemas import AuthUser
from app.core.exceptions import UnauthorizedError

bearer = HTTPBearer()


def verify_supabase_jwt(token: str) -> dict:
    """
    Decode and verify a Supabase-issued JWT.
    Returns the raw payload dict on success.
    Raises UnauthorizedError on failure.
    """
    settings = get_settings()

    # In production verify signature using the Supabase JWT secret.
    # For local development the dev token may be signed with ES256 (or other algs)
    # so allow unverified parsing to make local dev easier. This is intentionally
    # permissive and must not be used in production.
    if settings.is_production:
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},   # Supabase uses 'authenticated' audience
            )
            return payload
        except JWTError as e:
            raise UnauthorizedError(detail=f"Invalid or expired token: {e}")
    else:
        try:
            payload = jwt.get_unverified_claims(token)
            return payload
        except Exception as e:
            raise UnauthorizedError(detail=f"Invalid token (dev parse failed): {e}")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthUser:
    """
    FastAPI dependency — validates the Bearer token and returns the auth user.
    Usage in a router:  user: AuthUser = Depends(get_current_user)
    """
    payload = verify_supabase_jwt(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError(detail="Token missing user id")
    return AuthUser(id=user_id, email=payload.get("email", ""))
