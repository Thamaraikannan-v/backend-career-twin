from supabase import create_client, Client
from app.config import get_settings
from functools import lru_cache


@lru_cache
def get_db() -> Client:
    """
    Singleton Supabase client using the service role key.
    Service role bypasses RLS — safe for backend-only use.
    Never expose this client or key to the frontend.
    """
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_key)
