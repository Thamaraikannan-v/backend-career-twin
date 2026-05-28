from app.core.models import call_model, extract_json
import structlog

log = structlog.get_logger()

PROMPT = """
You are an expert technical recruiter and career analyst.
Analyze this resume and extract a structured candidate profile.
Be specific — use actual details from the resume, not generic labels.

RESUME:
{resume_text}

Return ONLY valid JSON, no prose, no markdown fences:
{{
  "seniority_level": "junior | mid | senior | staff | principal",
  "years_experience": <int>,
  "top_skills": ["skill1", "skill2"],
  "leadership_score": "low | medium | high",
  "backend_strength": "weak | moderate | strong | expert",
  "career_stability": "low | moderate | high",
  "ownership_signals": ["evidence directly quoted from resume"],
  "career_narrative": "<2 sentences summarising their arc>",
  "domain_expertise": ["e.g. fintech", "infra", "ml"],
  "red_flags": ["gaps, short tenures, vague impact"],
  "strongest_achievement": "<single best achievement from resume>"
}}
"""


async def run(state: dict) -> dict:
    log.info("resume_agent_start")
    try:
        raw = await call_model(PROMPT.format(resume_text=state["resume_text"]))
        profile = extract_json(raw)
        log.info("resume_agent_done", seniority=profile.get("seniority_level"))
        return {"candidate_profile": profile}
    except Exception as e:
        log.error("resume_agent_failed", error=str(e))
        return {"candidate_profile": None, "error": str(e)}
