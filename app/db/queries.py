"""
All raw Supabase table calls live here.
Features import from this module — never call get_db() directly in a feature.
"""
from uuid import uuid4
from app.db.client import get_db
import structlog

log = structlog.get_logger()


def _safe_single(table_query) -> dict | None:
    """
    Safely execute a query that returns 0 or 1 rows.
    Avoids maybe_single() which throws on 204 in postgrest-py.
    """
    try:
        result = table_query.limit(1).execute()
        if result is None:
            return None
        data = getattr(result, "data", None)
        if data and len(data) > 0:
            return data[0]
        return None
    except Exception as e:
        log.error("db_safe_single_failed", error=str(e))
        return None


# ── Analysis queries ─────────────────────────────────────────────────────────

async def create_analysis(user_id: str, company_name: str, jd_text: str) -> str:
    analysis_id = str(uuid4())
    get_db().table("analyses").insert({
        "id":           analysis_id,
        "user_id":      user_id,
        "status":       "running",
        "company_name": company_name,
        "jd_text":      jd_text,
    }).execute()
    log.info("analysis_created", id=analysis_id, user=user_id)
    return analysis_id


async def update_analysis(analysis_id: str, state: dict, status: str = "done") -> None:
    get_db().table("analyses").update({
        "status":               status,
        "candidate_profile":    state.get("candidate_profile"),
        "jd_signals":           state.get("jd_signals"),
        "company_intel":        state.get("company_intel"),
        "recruiter_simulation": state.get("recruiter_simulation"),
        "resume_rewrite":       state.get("resume_rewrite"),
        "keyword_analysis":     state.get("keyword_analysis"),
        "baseline_score":       state.get("baseline_score"),
    }).eq("id", analysis_id).execute()
    log.info("analysis_updated", id=analysis_id, status=status)


async def get_analysis(analysis_id: str, user_id: str) -> dict | None:
    return _safe_single(
        get_db().table("analyses")
        .select("*")
        .eq("id", analysis_id)
        .eq("user_id", user_id)
    )


async def list_analyses(user_id: str) -> list[dict]:
    try:
        result = (
            get_db().table("analyses")
            .select("id, status, company_name, baseline_score, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        log.error("list_analyses_failed", error=str(e))
        return []


async def count_done_analyses(user_id: str) -> int:
    try:
        result = (
            get_db().table("analyses")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("status", "done")
            .execute()
        )
        return result.count or 0
    except Exception as e:
        log.error("count_analyses_failed", error=str(e))
        return 0


async def set_resume_url(analysis_id: str, resume_url: str) -> None:
    try:
        get_db().table("analyses").update(
            {"resume_url": resume_url}
        ).eq("id", analysis_id).execute()
    except Exception as e:
        log.error("set_resume_url_failed", error=str(e))


async def set_resume_text(analysis_id: str, resume_text: str) -> None:
    """Store parsed resume text for later retrieval."""
    try:
        get_db().table("analyses").update(
            {"resume_text": resume_text}
        ).eq("id", analysis_id).execute()
    except Exception as e:
        log.error("set_resume_text_failed", error=str(e))


async def get_latest_resume_metadata(user_id: str) -> dict | None:
    """
    Fetch metadata (char_count, created_at) of the latest saved resume.
    Calculates char_count from resume_text length since DB column doesn't exist yet.
    Returns dict with resume_char_count and created_at, or None if no resume found.
    """
    try:
        result = (
            get_db().table("analyses")
            .select("resume_text, created_at")
            .eq("user_id", user_id)
            .not_.is_("resume_text", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result and result.data and len(result.data) > 0:
            row = result.data[0]
            return {
                "resume_char_count": len(row.get("resume_text", "")),
                "created_at": row.get("created_at"),
            }
        return None
    except Exception as e:
        log.error("get_latest_resume_metadata_failed", error=str(e))
        return None


# ── Subscription queries ─────────────────────────────────────────────────────

async def get_subscription(user_id: str) -> dict | None:
    return _safe_single(
        get_db().table("subscriptions")
        .select("*")
        .eq("user_id", user_id)
    )


async def upsert_subscription(
    user_id: str,
    stripe_customer: str,
    stripe_sub_id: str,
    status: str,
) -> None:
    get_db().table("subscriptions").upsert({
        "user_id":         user_id,
        "stripe_customer": stripe_customer,
        "stripe_sub_id":   stripe_sub_id,
        "status":          status,
    }, on_conflict="user_id").execute()
    log.info("subscription_upserted", user=user_id, status=status)


async def get_subscription_by_stripe_customer(stripe_customer: str) -> dict | None:
    return _safe_single(
        get_db().table("subscriptions")
        .select("*")
        .eq("stripe_customer", stripe_customer)
    )


async def is_pro(user_id: str) -> bool:
    try:
        sub = await get_subscription(user_id)
        return sub is not None and sub.get("status") == "pro"
    except Exception:
        return False  # safe fallback — treat as free tier


# ── Resume queries ──────────────────────────────────────────────────────────

async def get_latest_resume(user_id: str) -> dict | None:
    """
    Fetch the most recently uploaded resume for a user.
    Returns dict with resume_url and parsed_text, or None if no resume found.
    """
    return _safe_single(
        get_db().table("analyses")
        .select("resume_url, resume_text")
        .eq("user_id", user_id)
        .not_.is_("resume_text", "null")
        .order("created_at", desc=True)
    )