from pydantic import BaseModel


class AuthUser(BaseModel):
    id: str
    email: str


class TokenPayload(BaseModel):
    sub: str        # Supabase user id
    email: str = ""
    exp: int = 0
