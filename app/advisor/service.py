import json
import asyncio
import time
from collections import Counter
from app.db.client import get_db
from app.core.models import call_model, extract_json
import structlog

log = structlog.get_logger()


# ── In-Memory TTL Cache ───────────────────────────────────────────────────────
# Prevents redundant LLM calls when the same user hits multiple endpoints
# in a short window (e.g. frontend loading snapshot + analysis + plan at once).
#
# TTL is conservative (5 min) — trajectory/gaps don't change per-refresh.
# Cache is per-process; fine for a single-server deployment.
# Swap for Redis if you go multi-process.

_CACHE: dict[str, tuple[float, any]] = {}  # key -> (expires_at, value)
CACHE_TTL_SECONDS = 300  # 5 minutes


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and time.monotonic() < entry[0]:
        return entry[1]
    if entry:
        del _CACHE[key]
    return None


def _cache_set(key: str, value, ttl: int = CACHE_TTL_SECONDS):
    _CACHE[key] = (time.monotonic() + ttl, value)


def _cache_invalidate(user_id: str):
    """Call this when new analysis/emails are added for a user."""
    keys_to_delete = [k for k in _CACHE if k.startswith(f"{user_id}:")]
    for k in keys_to_delete:
        del _CACHE[k]
    log.info("cache_invalidated", user_id=user_id, keys_removed=len(keys_to_delete))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_candidate_summary(candidate_profile: dict) -> str:
    """Build a structured, dynamic candidate summary from the profile dict."""
    if not candidate_profile or not isinstance(candidate_profile, dict):
        return "No candidate profile available."

    lines = []

    name = candidate_profile.get("name") or candidate_profile.get("full_name")
    if name:
        lines.append(f"Name: {name}")

    current_title = candidate_profile.get("current_title") or candidate_profile.get("title")
    if current_title:
        lines.append(f"Current Title: {current_title}")

    years_exp = (
        candidate_profile.get("years_experience")
        or candidate_profile.get("total_experience_years")
    )
    if years_exp is not None:
        lines.append(f"Years of Experience: {years_exp}")

    level = candidate_profile.get("seniority_level") or candidate_profile.get("level")
    if level:
        lines.append(f"Current Level: {level}")

    skills = candidate_profile.get("skills") or candidate_profile.get("top_skills") or []
    if skills:
        if isinstance(skills, list):
            lines.append(f"Skills: {', '.join(str(s) for s in skills[:15])}")
        else:
            lines.append(f"Skills: {str(skills)[:300]}")

    education = (
        candidate_profile.get("education") or candidate_profile.get("highest_education")
    )
    if education:
        if isinstance(education, list):
            lines.append(f"Education: {'; '.join(str(e) for e in education[:3])}")
        else:
            lines.append(f"Education: {str(education)[:200]}")

    location = candidate_profile.get("location") or candidate_profile.get("city")
    if location:
        lines.append(f"Location: {location}")

    summary = candidate_profile.get("summary") or candidate_profile.get("bio")
    if summary:
        lines.append(f"Summary: {str(summary)[:300]}")

    recent_roles = (
        candidate_profile.get("recent_roles")
        or candidate_profile.get("work_experience")
        or []
    )
    if recent_roles and isinstance(recent_roles, list):
        role_strs = []
        for r in recent_roles[:3]:
            if isinstance(r, dict):
                role_strs.append(
                    f"{r.get('title', '')} at {r.get('company', '')} ({r.get('duration', '')})"
                )
            else:
                role_strs.append(str(r))
        if role_strs:
            lines.append(f"Recent Roles: {'; '.join(role_strs)}")

    return "\n".join(lines) if lines else str(candidate_profile)[:500]


def _build_learning_style(candidate_profile: dict) -> str:
    """Derive learning style signals dynamically from the candidate profile."""
    if not candidate_profile or not isinstance(candidate_profile, dict):
        return "Learning style: unknown — no profile data available."

    signals = []
    skills = candidate_profile.get("skills") or []

    if isinstance(skills, list) and skills:
        skill_blob = " ".join(str(s).lower() for s in skills)
        if any(k in skill_blob for k in ["react", "next", "fastapi", "django", "flask", "express"]):
            signals.append("hands-on builder — learns by shipping full-stack projects")
        if any(k in skill_blob for k in ["rag", "llm", "fine-tun", "langchain", "embeddings"]):
            signals.append("deep technical learner — thrives with cutting-edge AI/ML material")
        if any(k in skill_blob for k in ["pytorch", "tensorflow", "sklearn", "numpy"]):
            signals.append("research-oriented — comfortable with academic papers and notebooks")

    recent_roles = (
        candidate_profile.get("recent_roles") or candidate_profile.get("work_experience") or []
    )
    if isinstance(recent_roles, list) and recent_roles:
        signals.append("learns well through real-world project ownership")

    years_exp = (
        candidate_profile.get("years_experience")
        or candidate_profile.get("total_experience_years")
    )
    if years_exp is not None:
        try:
            yrs = float(years_exp)
            if yrs < 2:
                signals.append("early career — benefits from structured courses and mentorship")
            elif yrs < 5:
                signals.append("mid-growth stage — ready for project-led and peer learning")
            else:
                signals.append("experienced — learns best through challenges and system-level thinking")
        except (ValueError, TypeError):
            pass

    education = candidate_profile.get("education") or []
    if isinstance(education, list):
        edu_str = " ".join(str(e).lower() for e in education)
        if "computer science" in edu_str or "engineering" in edu_str:
            signals.append("strong CS fundamentals — can handle algorithm-heavy material")

    if not signals:
        signals.append("learning style not determined — defaulting to project-based approach")

    return "\n- ".join(["Learning Style Signals:"] + signals)


def _format_rejection_emails(rejection_emails: list) -> str:
    if not rejection_emails:
        return "No rejection emails found."
    lines = []
    for e in rejection_emails[:10]:
        lines.append(
            f"- Company: {e.get('company_name') or 'Unknown'} | "
            f"Role: {e.get('role_title') or 'Unknown'} | "
            f"Intent: {e.get('intent') or ''} | "
            f"Urgency: {e.get('urgency') or ''}\n"
            f"  Summary: {e.get('summary') or ''}"
        )
    return "\n".join(lines)


def _format_skill_gaps(gaps: list) -> str:
    if not gaps:
        return "No skill gaps identified."
    return "\n".join(
        f"- {g.get('skill', 'Unknown')}: "
        f"importance={g.get('importance', '?')}/10, "
        f"current={g.get('current', '?')}/10, "
        f"required={g.get('required', '?')}/10"
        for g in gaps
    )


def _format_rejection_patterns(patterns: list) -> str:
    if not patterns:
        return "No rejection patterns identified."
    return "\n".join(
        f"- {p.get('reason', 'Unknown')} (×{p.get('frequency', 0)}): {p.get('evidence', '')}"
        for p in patterns
    )


# ── Prompts ───────────────────────────────────────────────────────────────────

ANALYZE_REJECTIONS_PROMPT = """
Analyze these job rejection emails and rejection reasons for a specific candidate.
Identify patterns: why is this candidate being rejected repeatedly?

CANDIDATE PROFILE:
{candidate_summary}

REJECTION EMAILS:
{rejection_emails}

ANALYSIS CONTEXT (from job analyses):
{analysis_context}

Return ONLY JSON:
{{
  "patterns": [
    {{"reason": "seniority gap", "frequency": 3, "evidence": "roles ask for mid-level, candidate is applying as junior"}},
    {{"reason": "skill gap: TypeScript", "frequency": 2, "evidence": "2 rejections explicitly mentioned missing TypeScript"}}
  ],
  "strengths": ["list inferred from their profile"],
  "blockers": ["list inferred from rejections and profile"],
  "current_level": "inferred from profile (e.g. junior, mid, senior)",
  "target_level": "inferred from roles they are applying to",
  "key_insight": "One-sentence summary of the core issue and recommended fix."
}}
"""

SKILL_GAP_PROMPT = """
Given this candidate's profile and their target role level, identify the most important skill gaps.

CANDIDATE PROFILE:
{candidate_summary}

TARGET LEVEL: {target_level}
TARGET ROLES: {target_roles}

Return ONLY JSON:
{{
  "gaps": [
    {{"skill": "TypeScript", "importance": 9, "current": 3, "required": 8, "rationale": "Most target roles list it as required"}},
    {{"skill": "System Design", "importance": 8, "current": 4, "required": 8, "rationale": "Mid-level interviews always test this"}}
  ]
}}

importance: how critical for the target role (1-10)
current: candidate's current estimated level (1-10)
required: minimum needed for target level (1-10)
rationale: one sentence explaining why this gap matters for this specific candidate
"""

TRAJECTORY_PROMPT = """
Predict this candidate's career trajectory to reach their target level.

CANDIDATE PROFILE:
{candidate_summary}

SKILL GAPS:
{skill_gaps}

REJECTION PATTERNS:
{rejection_patterns}

Based on this candidate's specific background and gaps, estimate:
- How many weeks to close the critical gaps
- Key milestones with concrete signals of readiness
- Confidence level in the timeline

Return ONLY JSON:
{{
  "estimated_weeks": 8,
  "confidence": 0.72,
  "rationale": "Explain reasoning grounded in THIS candidate's specific background and gaps.",
  "milestones": [
    {{
      "week": 2,
      "goal": "Goal title",
      "signal": "Concrete, observable signal they have reached this goal",
      "resources": ["specific resource 1", "specific resource 2"]
    }}
  ]
}}
"""

LEARNING_PLAN_PROMPT = """
Generate a personalized learning plan for this candidate to close their skill gaps.

CANDIDATE PROFILE:
{candidate_summary}

SKILL GAPS:
{skill_gaps}

{learning_style}

Return ONLY JSON:
{{
  "resources": [
    {{
      "title": "Course or resource title",
      "type": "course | book | practice | project | video",
      "duration_hours": 20,
      "difficulty": "beginner | intermediate | advanced",
      "skill_targets": ["Skill1", "Skill2"],
      "why_this": "Personalized explanation referencing the candidate's specific background."
    }}
  ],
  "projects": [
    "Project description (N weeks) - signals X skill"
  ],
  "interviews": [
    "Mock interview question or scenario"
  ]
}}
"""

JOB_READINESS_PROMPT = """
Score this candidate's readiness for a specific job.

CANDIDATE PROFILE:
{candidate_summary}

SKILL GAPS (vs target level):
{skill_gaps}

JOB:
Title: {job_title}
Company: {company}
Description: {job_description}

Score their readiness for THIS specific role, not just the general level.

Return ONLY JSON:
{{
  "match_score": 72,
  "ready_now": false,
  "ready_in_weeks": 4,
  "confidence": 0.68,
  "gaps": ["specific gap 1 for this role", "specific gap 2"],
  "strengths_for_this_role": ["what the candidate already has that this role needs"],
  "how_to_get_ready": [
    "Concrete step 1 (time estimate)",
    "Concrete step 2 (time estimate)"
  ]
}}
"""


# ── DB Fetchers (cached, no LLM) ──────────────────────────────────────────────

async def _get_candidate_profile(user_id: str) -> tuple[dict, str]:
    """
    Fetch candidate profile from latest analysis. Cached per user.
    Returns (raw_dict, formatted_summary_string).
    """
    cache_key = f"{user_id}:candidate_profile"
    cached = _cache_get(cache_key)
    if cached is not None:
        log.debug("cache_hit", key=cache_key)
        return cached

    analysis_result = (
        get_db().table("analyses")
        .select("candidate_profile, jd_signals")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not analysis_result.data:
        return {}, "No candidate profile available."

    raw = analysis_result.data[0].get("candidate_profile") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = {}

    jd_signals = analysis_result.data[0].get("jd_signals") or {}

    summary = _build_candidate_summary(raw)
    result = (raw, summary, jd_signals)
    _cache_set(cache_key, result)
    return result


async def _get_target_roles(user_id: str) -> list[str]:
    """Fetch user's recent job search roles. Cached per user."""
    cache_key = f"{user_id}:target_roles"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        search_result = (
            get_db().table("job_searches")
            .select("role")
            .eq("user_id", user_id)
            .order("searched_at", desc=True)
            .limit(10)
            .execute()
        )
        roles = list({
            s.get("role", "").strip()
            for s in (search_result.data or [])
            if s.get("role")
        })
        _cache_set(cache_key, roles)
        return roles
    except Exception as e:
        log.warning("target_roles_fetch_failed", error=str(e))
        return []


async def _get_rejection_emails(user_id: str) -> tuple[list, list]:
    """
    Fetch recruiter emails for this user. Cached per user.
    Returns (all_emails, rejection_emails_only).
    """
    cache_key = f"{user_id}:emails"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    emails_result = (
        get_db().table("recruiter_emails")
        .select("intent, company_name, role_title, summary, urgency")
        .eq("user_id", user_id)
        .eq("is_recruiter", True)
        .execute()
    )
    emails = emails_result.data or []
    rejections = [e for e in emails if e.get("intent") == "rejection"]
    result = (emails, rejections)
    _cache_set(cache_key, result)
    return result


async def _infer_target_level(candidate_profile: dict, target_roles: list[str]) -> str:
    """Infer target level from profile fields, then years of exp, then role titles."""
    explicit = (
        candidate_profile.get("target_level")
        or candidate_profile.get("desired_level")
        or candidate_profile.get("goal_level")
    )
    if explicit:
        return str(explicit).lower()

    years_exp = (
        candidate_profile.get("years_experience")
        or candidate_profile.get("total_experience_years")
    )
    if years_exp is not None:
        try:
            yrs = float(years_exp)
            if yrs < 2:
                return "junior"
            elif yrs < 5:
                return "mid"
            elif yrs < 8:
                return "senior"
            else:
                return "staff"
        except (ValueError, TypeError):
            pass

    if target_roles:
        roles_lower = " ".join(target_roles).lower()
        if "senior" in roles_lower or "sr." in roles_lower:
            return "senior"
        if "staff" in roles_lower or "principal" in roles_lower:
            return "staff"
        if "junior" in roles_lower or "jr." in roles_lower:
            return "junior"

    return "mid"


# ── LLM Functions (each runs exactly one LLM call) ────────────────────────────
# These accept pre-fetched data so callers can share DB results without
# re-fetching. The public API wrappers below handle the orchestration.

async def _llm_analyze_rejections(
    user_id: str,
    candidate_summary: str,
    jd_signals,
    rejection_emails: list,
) -> dict:
    """Single LLM call: rejection pattern analysis. Cached per user."""
    cache_key = f"{user_id}:rejection_analysis"
    cached = _cache_get(cache_key)
    if cached is not None:
        log.debug("cache_hit", key=cache_key)
        return cached

    if isinstance(jd_signals, dict):
        context_str = json.dumps(jd_signals)[:400]
    else:
        context_str = str(jd_signals)[:400] if jd_signals else "No additional context."

    prompt = ANALYZE_REJECTIONS_PROMPT.format(
        candidate_summary=candidate_summary,
        rejection_emails=_format_rejection_emails(rejection_emails),
        analysis_context=context_str,
    )
    raw = await call_model(prompt)
    result = extract_json(raw)
    _cache_set(cache_key, result)
    log.info("rejections_analyzed", count=len(rejection_emails), cached=False)
    return result


async def _llm_analyze_skill_gaps(
    user_id: str,
    candidate_summary: str,
    target_level: str,
    target_roles: list[str],
) -> dict:
    """Single LLM call: skill gap analysis. Cached per user + level."""
    cache_key = f"{user_id}:skill_gaps:{target_level}"
    cached = _cache_get(cache_key)
    if cached is not None:
        log.debug("cache_hit", key=cache_key)
        return cached

    roles_str = (
        ", ".join(target_roles) if target_roles
        else f"{target_level.capitalize()}-level engineer"
    )
    prompt = SKILL_GAP_PROMPT.format(
        candidate_summary=candidate_summary,
        target_level=target_level,
        target_roles=roles_str,
    )
    raw = await call_model(prompt)
    result = extract_json(raw)
    _cache_set(cache_key, result)
    log.info("skill_gaps_analyzed", target_level=target_level, role_count=len(target_roles), cached=False)
    return result


async def _llm_predict_trajectory(
    user_id: str,
    candidate_summary: str,
    gaps: dict,
    rejections: dict,
    target_level: str,
) -> dict:
    """Single LLM call: trajectory prediction. Cached per user."""
    cache_key = f"{user_id}:trajectory"
    cached = _cache_get(cache_key)
    if cached is not None:
        log.debug("cache_hit", key=cache_key)
        return cached

    prompt = TRAJECTORY_PROMPT.format(
        candidate_summary=candidate_summary,
        skill_gaps=_format_skill_gaps(gaps.get("gaps", [])),
        rejection_patterns=_format_rejection_patterns(rejections.get("patterns", [])),
    )
    raw = await call_model(prompt)
    result = extract_json(raw)
    _cache_set(cache_key, result)
    log.info("trajectory_predicted", weeks=result.get("estimated_weeks"), level=target_level, cached=False)
    return result


# ── Public API ─────────────────────────────────────────────────────────────────
# Each function fetches only what it needs, passes data down to LLM helpers,
# and never triggers a duplicate LLM call thanks to the cache layer.

async def analyze_rejections(user_id: str) -> dict:
    """Analyze rejection patterns for a user."""
    try:
        profile_data = await _get_candidate_profile(user_id)
        candidate_profile, candidate_summary, jd_signals = profile_data
        _, rejection_emails = await _get_rejection_emails(user_id)
        return await _llm_analyze_rejections(
            user_id, candidate_summary, jd_signals, rejection_emails
        )
    except Exception as e:
        log.error("rejection_analysis_failed", error=str(e))
        return {}


async def analyze_skill_gaps(user_id: str, target_level: str | None = None) -> dict:
    """Identify skill gaps. target_level is inferred if not provided."""
    try:
        profile_data = await _get_candidate_profile(user_id)
        candidate_profile, candidate_summary, _ = profile_data
        target_roles = await _get_target_roles(user_id)
        resolved_level = target_level or await _infer_target_level(candidate_profile, target_roles)
        return await _llm_analyze_skill_gaps(
            user_id, candidate_summary, resolved_level, target_roles
        )
    except Exception as e:
        log.error("skill_gap_analysis_failed", error=str(e))
        return {}


async def predict_trajectory(user_id: str) -> dict:
    """Predict career trajectory. Reuses cached rejections + gaps — no duplicate calls."""
    try:
        profile_data = await _get_candidate_profile(user_id)
        candidate_profile, candidate_summary, jd_signals = profile_data
        target_roles = await _get_target_roles(user_id)
        target_level = await _infer_target_level(candidate_profile, target_roles)
        _, rejection_emails = await _get_rejection_emails(user_id)

        # These are cached — if snapshot already called them, zero extra LLM calls here
        rejections, gaps = await asyncio.gather(
            _llm_analyze_rejections(user_id, candidate_summary, jd_signals, rejection_emails),
            _llm_analyze_skill_gaps(user_id, candidate_summary, target_level, target_roles),
        )

        return await _llm_predict_trajectory(
            user_id, candidate_summary, gaps, rejections, target_level
        )
    except Exception as e:
        log.error("trajectory_prediction_failed", error=str(e))
        return {}


async def generate_learning_plan(user_id: str) -> dict:
    """Generate personalized learning plan. Reuses cached gaps."""
    try:
        profile_data = await _get_candidate_profile(user_id)
        candidate_profile, candidate_summary, _ = profile_data
        target_roles = await _get_target_roles(user_id)
        target_level = await _infer_target_level(candidate_profile, target_roles)

        # Cached if analyze_skill_gaps was already called
        gaps = await _llm_analyze_skill_gaps(
            user_id, candidate_summary, target_level, target_roles
        )
        learning_style = _build_learning_style(candidate_profile)

        prompt = LEARNING_PLAN_PROMPT.format(
            candidate_summary=candidate_summary,
            skill_gaps=_format_skill_gaps(gaps.get("gaps", [])),
            learning_style=learning_style,
        )
        raw = await call_model(prompt)
        plan = extract_json(raw)
        log.info("learning_plan_generated", target_level=target_level)
        return plan

    except Exception as e:
        log.error("learning_plan_generation_failed", error=str(e))
        return {}


async def score_job_readiness(user_id: str, job_listing_id: str) -> dict:
    """Score readiness for a specific job. Reuses cached gaps."""
    try:
        job_result = (
            get_db().table("job_listings")
            .select("*")
            .eq("id", job_listing_id)
            .limit(1)
            .execute()
        )
        job = job_result.data[0] if job_result.data else {}

        profile_data = await _get_candidate_profile(user_id)
        candidate_profile, candidate_summary, _ = profile_data
        target_roles = await _get_target_roles(user_id)
        target_level = await _infer_target_level(candidate_profile, target_roles)

        gaps = await _llm_analyze_skill_gaps(
            user_id, candidate_summary, target_level, target_roles
        )

        prompt = JOB_READINESS_PROMPT.format(
            candidate_summary=candidate_summary,
            skill_gaps=_format_skill_gaps(gaps.get("gaps", [])),
            job_title=job.get("title") or "Unknown Role",
            company=job.get("company") or "Unknown Company",
            job_description=(job.get("description_snippet") or job.get("description") or "")[:400],
        )
        raw = await call_model(prompt)
        readiness = extract_json(raw)
        log.info("job_readiness_scored", job=job.get("title"), match_score=readiness.get("match_score"))
        return readiness

    except Exception as e:
        log.error("job_readiness_scoring_failed", error=str(e))
        return {}


async def get_career_snapshot(user_id: str) -> dict:
    """
    Full career snapshot. Orchestrates all sub-calls in parallel where possible.
    On first load: 3 LLM calls total (rejections, gaps, trajectory).
    On refresh within TTL: 0 LLM calls — all served from cache.
    """
    try:
        # ── DB fetches (no LLM, fast) ─────────────────────────────────────────
        profile_data = await _get_candidate_profile(user_id)
        candidate_profile, candidate_summary, jd_signals = profile_data
        target_roles, (all_emails, rejection_emails) = await asyncio.gather(
            _get_target_roles(user_id),
            _get_rejection_emails(user_id),
        )
        target_level = await _infer_target_level(candidate_profile, target_roles)

        # ── LLM calls in parallel (cached after first run) ───────────────────
        rejections, gaps = await asyncio.gather(
            _llm_analyze_rejections(user_id, candidate_summary, jd_signals, rejection_emails),
            _llm_analyze_skill_gaps(user_id, candidate_summary, target_level, target_roles),
        )
        # trajectory reuses rejections + gaps from cache — no extra LLM calls
        trajectory = await _llm_predict_trajectory(
            user_id, candidate_summary, gaps, rejections, target_level
        )

        # ── Stats (no LLM) ────────────────────────────────────────────────────
        total_apps = len(all_emails)
        rejection_rate = len(rejection_emails) / max(total_apps, 1)

        rejection_targets = [
            f"{e.get('company_name', 'Unknown')} — {e.get('role_title', 'Unknown')}"
            for e in rejection_emails
        ]
        rejection_counts = Counter(rejection_targets)
        top_rejection_reasons = [
            {
                "reason": target,
                "frequency": count,
                "percentage": round(count / max(len(rejection_emails), 1), 2),
            }
            for target, count in rejection_counts.most_common(5)
        ]

        # ── FIX: job_listings has no user_id — join via saved_jobs ────────────
        saved_result = (
            get_db().table("saved_jobs")
            .select("job_listings(match_score)")
            .eq("user_id", user_id)
            .execute()
        )
        scores = [
            row["job_listings"]["match_score"]
            for row in (saved_result.data or [])
            if row.get("job_listings")
            and row["job_listings"].get("match_score") is not None
        ]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

        return {
            "total_applications": total_apps,
            "total_rejections": len(rejection_emails),
            "rejection_rate": round(rejection_rate, 2),
            "top_rejection_reasons": top_rejection_reasons,
            "avg_match_score": avg_score,
            "trajectory": trajectory,
        }

    except Exception as e:
        log.error("snapshot_generation_failed", error=str(e))
        return {
            "total_applications": 0,
            "total_rejections": 0,
            "rejection_rate": 0.0,
            "top_rejection_reasons": [],
            "avg_match_score": 0.0,
            "trajectory": {},
            "error": str(e),
        }