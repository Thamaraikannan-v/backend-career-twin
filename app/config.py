from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Gemini
    gemini_api_key: str = ""

    # Groq (main LLM — free, fast, 30 RPM)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Tavily (web search for company_agent)
    tavily_api_key: str = ""

    # LLM routing: "auto" | "groq" | "gemini"
    llm_provider: str = "groq"

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_pro_price_id: str = ""

    # Hunter.io (email finder — free 25 searches/month)
    hunter_api_key: str = ""

    # Exa (semantic job search)
    exa_api_key: str = ""


    # App
    app_env: str = "development"
    cors_origins: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://localhost:8080,"
        "http://127.0.0.1:3000,"
        "http://127.0.0.1:5173,"
        "http://127.0.0.1:8080"
    )
    free_tier_limit: int = 3

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

# Note: add these two lines inside the Settings class before the @property methods