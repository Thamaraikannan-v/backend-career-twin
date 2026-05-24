from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import get_settings
from app.analysis.router import router as analysis_router
from app.auth.router    import router as auth_router
from app.resume.router  import router as resume_router
# from app.billing.router import router as billing_router
from app.graph.graph    import get_graph
from app.recruiter_mail.router import router as recruiter_mail_router


import structlog

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Compile LangGraph once at startup — first request won't pay compile cost
    log.info("compiling_langgraph")
    get_graph()
    log.info("startup_complete", env=settings.app_env)
    yield
    log.info("shutdown")


app = FastAPI(
    title="Career Twin API",
    description="AI recruiter simulation — see yourself the way recruiters do.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis_router)
app.include_router(auth_router)
app.include_router(resume_router)
# app.include_router(billing_router)
app.include_router(recruiter_mail_router)  # recruiter_mail router

app.include_router(recruiter_mail_router)

@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "env": settings.app_env}


@app.get("/debug-env")
async def debug_env():
    s = get_settings()
    return {
        "supabase_url":     s.supabase_url[:30] + "..." if s.supabase_url else "MISSING",
        "service_key_len":  len(s.supabase_service_key) if s.supabase_service_key else 0,
        "service_key_start": s.supabase_service_key[:20] if s.supabase_service_key else "MISSING",
    }
