from app.core.models import call_model, extract_json
import structlog

log = structlog.get_logger()

PROMPT = """
You are a senior recruiter at {company_name} with 10+ years of hiring experience.
You are reviewing a candidate for the role below.

This is your INTERNAL monologue — completely honest, unfiltered, the thoughts
you would never say to the candidate's face. Be specific, not generic.
Reference actual details from their background.

─── CANDIDATE PROFILE ───────────────────────────────────────
{candidate_profile}

─── WHAT THIS ROLE ACTUALLY NEEDS ──────────────────────────
{jd_signals}

─── WHAT YOU KNOW ABOUT YOUR COMPANY ───────────────────────
{company_intel}

Return ONLY valid JSON:
{{
  "first_impression": "<gut reaction in 1 honest sentence>",
  "perceived_strengths": ["<specific strength with evidence>"],
  "silent_concerns": ["<concern you have but would never say aloud>"],
  "likely_rejection_reason": "<#1 most likely reason you pass on this person>",
  "culture_fit_score": <0-100>,
  "technical_fit_score": <0-100>,
  "hiring_confidence": <0-100>,
  "seniority_mismatch": "overqualified | underqualified | good fit",
  "questions_you_would_ask": ["<probing question targeting a weakness>"],
  "what_would_make_them_standout": "<what would flip this to a strong yes>",
  "resume_first_impression": "<what jumps out in the first 6 seconds>"
}}
"""


async def run(state: dict) -> dict:
    log.info("recruiter_agent_start")
    try:
        raw = await call_model(
            PROMPT.format(
                company_name=state["company_name"],
                candidate_profile=state.get("candidate_profile", {}),
                jd_signals=state.get("jd_signals", {}),
                company_intel=state.get("company_intel", {}),
            )
        )
        sim = extract_json(raw)
        log.info("recruiter_agent_done", confidence=sim.get("hiring_confidence"))
        return {"recruiter_simulation": sim}
    except Exception as e:
        log.error("recruiter_agent_failed", error=str(e))
        return {"recruiter_simulation": None, "error": str(e)}
