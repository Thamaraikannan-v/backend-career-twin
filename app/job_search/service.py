import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4

import structlog
from openai import OpenAI

from app.config import get_settings
from app.core.models import call_model, extract_json
from app.db.client import get_db

log = structlog.get_logger()

PARSE_AND_SCORE_PROMPT = """
You are a job listing parser and career coach.
Extract structured data from these search results AND score each job against the candidate profile.

CANDIDATE PROFILE:
{candidate_profile}

SEARCH RESULTS:
{results}

Return ONLY a valid JSON array with no markdown:
[
  {{
    "title": "exact job title",
    "company": "company name",
    "location": "city, country or Remote",
    "salary_min": <int or null>,
    "salary_max": <int or null>,
    "salary_currency": "USD|INR|GBP|null",
    "posted_date": "YYYY-MM-DD or null",
    "apply_url": "https://...",
    "description_snippet": "<first 200 chars of job description>",
    "job_type": "full-time|contract|remote|part-time|null",
    "is_remote": true|false,
    "match_score": <0-100>,
    "match_reason": "<1-2 sentences why this score>"
  }}
]

Scoring guide:
0-30: not a fit | 31-60: okay fit | 61-100: strong fit

Extract all numerical salary values as integers (e.g. "50,000" → 50000).
If salary range given, salary_min is lower, salary_max is higher.
posted_date must be YYYY-MM-DD format if present.
Return empty array [] if no valid jobs found.
"""


def _get_groq_mcp_client() -> OpenAI:
    """Return an OpenAI-compatible Groq client."""
    return OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=get_settings().groq_api_key,
    )


def _get_exa_mcp_tool() -> dict:
    """Build the Exa MCP tool config using the API key from settings."""
    settings = get_settings()
    return {
        "type": "mcp",
        "server_url": f"https://mcp.exa.ai/mcp?exaApiKey={settings.exa_api_key}",
        "server_label": "exa",
        "require_approval": "never",
    }


async def _search_via_mcp(
    role: str,
    location: str,
    days_old: int = 7,
    salary_min: int | None = None,
) -> str:
    """
    Use Groq + Exa MCP to run a semantic job search and LinkedIn-specific search.
    Returns the combined raw text from the model.
    """
    settings = get_settings()
    if not settings.exa_api_key:
        log.error("exa_api_key_not_set")
        return ""

    salary_hint = f" with salary above {salary_min}" if salary_min else ""
    days_hint = f" posted in the last {days_old} days" if days_old else ""

    prompt = (
        f"Search for {role} jobs in {location}{salary_hint}{days_hint}. "
        f"Use exa_search for a broad semantic search and linkedin_search to find "
        f"locations, salaries, apply URLs, and job descriptions. recent jobs should have posted_date within {days_old} days. "
    )

    log.info("mcp_search_start", role=role, location=location)

    def _call_groq() -> str:
        client = _get_groq_mcp_client()
        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=prompt,
            tools=[_get_exa_mcp_tool()],
            temperature=0.1,
            top_p=0.4,
        )
        return response.output_text or ""

    try:
        raw_text = await asyncio.to_thread(_call_groq)
        log.info("mcp_search_done", chars=len(raw_text))
        return raw_text
    except Exception as e:
        log.error("mcp_search_failed", error=str(e))
        return ""


async def _parse_and_score_jobs(raw_text: str, candidate_profile: str) -> list[dict]:
    """Parse and score all jobs in a single LLM call."""
    if not raw_text:
        return []

    prompt = PARSE_AND_SCORE_PROMPT.format(
        candidate_profile=candidate_profile,
        results=raw_text[:6000],
    )

    try:
        raw = await call_model(prompt)
        jobs = extract_json(raw)
        if isinstance(jobs, list):
            log.info("jobs_parsed_and_scored", count=len(jobs))
            return jobs
        return []
    except Exception as e:
        log.error("parse_and_score_failed", error=str(e))
        return []


async def _get_cached_search(user_id: str, role: str, location: str) -> dict | None:
    """Return cached search if found and less than 6 hours old."""
    try:
        six_hours_ago = datetime.utcnow() - timedelta(hours=6)
        result = (
            get_db()
            .table("job_searches")
            .select("*")
            .eq("user_id", user_id)
            .ilike("role", role)
            .ilike("location", location)
            .gte("searched_at", six_hours_ago.isoformat())
            .order("searched_at", desc=True)
            .limit(1)
            .execute()
        )
        data = result.data
        if data:
            search = data[0]
            log.info("search_cache_hit", search_id=search["id"])
            jobs_result = (
                get_db()
                .table("job_listings")
                .select("*")
                .eq("search_id", search["id"])
                .order("match_score", desc=True)
                .execute()
            )
            return {"search": search, "jobs": jobs_result.data or []}
        return None
    except Exception as e:
        log.warning("cache_check_failed", error=str(e))
        return None


async def _get_candidate_profile(user_id: str) -> str:
    """Fetch candidate's profile from most recent analysis."""
    try:
        result = (
            get_db()
            .table("analyses")
            .select("candidate_profile")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        data = result.data
        if data:
            profile = data[0].get("candidate_profile", {})
            return json.dumps(profile) if isinstance(profile, dict) else str(profile)
    except Exception as e:
        log.warning("profile_fetch_failed", error=str(e))

    return "Experienced software professional seeking new opportunities."


async def search_jobs(
    user_id: str,
    role: str,
    location: str,
    days_old: int = 7,
    salary_min: int | None = None,
    company: str | None = None,
) -> dict:
    """
    Full job search pipeline:
    1. Check 6-hour cache
    2. Groq + Exa MCP search (semantic + LinkedIn in one call)
    3. Parse + score in a single LLM call
    4. Dedup + store
    """
    # 1. Cache check
    cached = await _get_cached_search(user_id, role, location)
    if cached:
        return {
            "search_id": cached["search"]["id"],
            "query": f"{role} in {location}",
            "jobs": cached["jobs"],
            "total_count": len(cached["jobs"]),
            "from_cache": True,
        }

    log.info("job_search_start", role=role, location=location, days_old=days_old)

    # 2. MCP search
    raw_text = await _search_via_mcp(role, location, days_old, salary_min)
    if not raw_text:
        log.warning("no_jobs_found", role=role, location=location)
        return {
            "search_id": str(uuid4()),
            "query": f"{role} in {location}",
            "jobs": [],
            "total_count": 0,
            "from_cache": False,
        }

    # 3. Candidate profile
    candidate_profile = await _get_candidate_profile(user_id)

    # 4. Parse + score in one LLM call
    jobs = await _parse_and_score_jobs(raw_text, candidate_profile)

    # 5. Dedup by (title, company)
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for job in jobs:
        key = (job.get("title", "").lower(), job.get("company", "").lower())
        if key not in seen:
            seen.add(key)
            deduped.append(job)

    # 6. Store search + job listings
    search_id = str(uuid4())
    now = datetime.utcnow().isoformat()
    stored_jobs = []  # ← collect enriched jobs to return

    try:
        get_db().table("job_searches").insert(
            {
                "id": search_id,
                "user_id": user_id,
                "role": role,
                "location": location,
                "days_old": days_old,
                "salary_min": salary_min,
                "company_filter": company,
                "result_count": len(deduped),
            }
        ).execute()

        for job in deduped:
            job_id = str(uuid4())
            # Normalise posted_date → posted_at
            posted_at = job.get("posted_date") or job.get("posted_at")

            get_db().table("job_listings").insert(
                {
                    "id": job_id,
                    "search_id": search_id,
                    "title": job.get("title"),
                    "company": job.get("company"),
                    "location": job.get("location"),
                    "salary_min": job.get("salary_min"),
                    "salary_max": job.get("salary_max"),
                    "salary_currency": job.get("salary_currency", "USD"),
                    "job_type": job.get("job_type"),
                    "posted_at": posted_at,
                    "apply_url": job.get("apply_url"),
                    "description_snippet": job.get("description_snippet"),
                    "source": "exa_mcp",
                    "is_remote": job.get("is_remote", False),
                    "match_score": job.get("match_score", 50),
                    "match_reason": job.get("match_reason", ""),
                    "created_at": now,
                }
            ).execute()

            # Enrich job dict with DB fields so response matches JobListing schema
            stored_jobs.append({
                **job,
                "id": job_id,
                "posted_at": posted_at,
                "source": "exa_mcp",
                "created_at": now,
            })

        log.info("jobs_stored", search_id=search_id, count=len(deduped))
    except Exception as e:
        log.error("jobs_store_failed", error=str(e))
        # Still enrich even if DB write failed so response doesn't 500
        stored_jobs = [
            {
                **job,
                "id": str(uuid4()),
                "posted_at": job.get("posted_date") or job.get("posted_at"),
                "source": "exa_mcp",
                "created_at": now,
            }
            for job in deduped
        ]

    # 7. Sort: highest match score first, then most recent
    stored_jobs.sort(key=lambda x: (-x.get("match_score", 0), x.get("posted_date", "") or ""))

    return {
        "search_id": search_id,
        "query": f"{role} in {location}",
        "jobs": stored_jobs,   # ← enriched, schema-compliant
        "total_count": len(stored_jobs),
        "from_cache": False,
    }


async def save_job(user_id: str, job_listing_id: str, status: str) -> bool:
    """Save or update a job's status (saved/applied/interviewing/rejected)."""
    try:
        existing = (
            get_db()
            .table("saved_jobs")
            .select("id")
            .eq("user_id", user_id)
            .eq("job_listing_id", job_listing_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            get_db().table("saved_jobs").update({"status": status}).eq(
                "id", existing.data[0]["id"]
            ).execute()
        else:
            get_db().table("saved_jobs").insert(
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "job_listing_id": job_listing_id,
                    "status": status,
                }
            ).execute()

        log.info("job_saved", user=user_id, job=job_listing_id, status=status)
        return True
    except Exception as e:
        log.error("save_job_failed", error=str(e))
        return False


async def get_saved_jobs(user_id: str) -> list[dict]:
    """Get user's saved/applied/interviewing jobs."""
    try:
        result = (
            get_db()
            .table("saved_jobs")
            .select("job_listing_id, status, saved_at, job_listings(*)")
            .eq("user_id", user_id)
            .order("saved_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        log.error("get_saved_jobs_failed", error=str(e))
        return []


async def get_search_history(user_id: str) -> list[dict]:
    """Get user's past job searches."""
    try:
        result = (
            get_db()
            .table("job_searches")
            .select("id, role, location, days_old, result_count, searched_at")
            .eq("user_id", user_id)
            .order("searched_at", desc=True)
            .limit(20)
            .execute()
        )
        return result.data or []
    except Exception as e:
        log.error("get_history_failed", error=str(e))
        return []