from app.graph.graph import get_graph
from app.db import queries as db
from app.resume.service import parse_pdf, upload_to_storage
from app.core.exceptions import FreeTierLimitError, AnalysisNotFoundError
from app.config import get_settings
import structlog

log = structlog.get_logger()


async def start_analysis(
    user_id:      str,
    company_name: str,
    jd_text:      str,
    file_bytes:   bytes | None = None,
    resume_text:  str | None = None,
) -> tuple[str, str]:
    """
    Validates free tier, creates DB row, parses PDF, stores file.
    Returns (analysis_id, resume_text) tuple.
    
    If file_bytes is provided, parses and uploads it.
    Otherwise, if resume_text is provided, uses it directly (pre-parsed).
    If neither is provided, raises error.
    
    The graph runs in a background task — see router.py.
    """
    # Free tier check (skip if user is Pro)
    pro = await db.is_pro(user_id)
    if not pro:
        count = await db.count_done_analyses(user_id)
        if count >= get_settings().free_tier_limit:
            raise FreeTierLimitError()

    # Determine resume text source
    if file_bytes:
        # Parse PDF first — fail fast before creating DB row
        parsed = parse_pdf(file_bytes)
        text = parsed["text"]
    elif resume_text:
        # Use pre-parsed resume text
        text = resume_text
    else:
        raise ValueError("Either file_bytes or resume_text must be provided")

    # Create DB row with status=running
    analysis_id = await db.create_analysis(
        user_id=user_id,
        company_name=company_name,
        jd_text=jd_text,
    )

    # Store parsed resume text for later retrieval
    await db.set_resume_text(analysis_id, text)

    # Upload PDF to Supabase Storage only if file_bytes provided (non-fatal if it fails)
    if file_bytes:
        resume_url = await upload_to_storage(user_id, analysis_id, file_bytes)
        if resume_url:
            await db.set_resume_url(analysis_id, resume_url)

    return analysis_id, text


async def run_graph_and_persist(
    analysis_id:  str,
    resume_text:  str,
    jd_text:      str,
    company_name: str,
    user_id:      str,
) -> None:
    """
    Runs the full LangGraph pipeline and writes results to Supabase.
    Called as a FastAPI background task.
    """
    log.info("graph_start", id=analysis_id)
    try:
        graph = get_graph()
        result = await graph.ainvoke({
            "resume_text":  resume_text,
            "jd_text":      jd_text,
            "company_name": company_name,
            "user_id":      user_id,
            "analysis_id":  analysis_id,
        })
        await db.update_analysis(analysis_id, result, status="done")
        log.info("graph_done", id=analysis_id)

    except Exception as e:
        log.error("graph_failed", id=analysis_id, error=str(e))
        await db.update_analysis(analysis_id, {}, status="failed")


def _listify(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def _normalize_recruiter_simulation(raw: dict | None) -> dict | None:
    if raw is None:
        return None

    return {
        "rejection_reason": str(raw.get("rejection_reason") or raw.get("likely_rejection_reason") or "").strip(),
        "strengths": _listify(raw.get("strengths") or raw.get("perceived_strengths")),
        "concerns": _listify(raw.get("concerns") or raw.get("silent_concerns")),
    }


async def get_analysis(analysis_id: str, user_id: str) -> dict:
    """Fetch one analysis or raise 404."""
    row = await db.get_analysis(analysis_id, user_id)
    if not row:
        raise AnalysisNotFoundError()

    row["recruiter_simulation"] = _normalize_recruiter_simulation(
        row.get("recruiter_simulation")
    )
    return row


async def list_analyses(user_id: str) -> list[dict]:
    return await db.list_analyses(user_id)


async def get_latest_resume_for_user(user_id: str) -> dict | None:
    """Fetch the latest saved resume for a user."""
    return await db.get_latest_resume(user_id)


async def get_latest_resume_metadata_for_user(user_id: str) -> dict | None:
    """Fetch metadata (char_count, created_at) of latest saved resume."""
    return await db.get_latest_resume_metadata(user_id)
