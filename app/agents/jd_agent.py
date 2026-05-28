from app.core.models import call_model, extract_json
import structlog

log = structlog.get_logger()

PROMPT = """
You are an expert at decoding job descriptions — reading between the lines
to find what the company actually wants versus what they wrote.

JD:
{jd_text}

Return ONLY valid JSON:
{{
  "seniority_level": "junior | mid | senior | staff | principal",
  "must_have_skills": ["skill1"],
  "nice_to_have_skills": ["skill1"],
  "hidden_priorities": ["what the JD implies but never states directly"],
  "culture_signals": ["startup hustle expected", "heavy process", ...],
  "ambiguity_tolerance": "low | medium | high",
  "ownership_expectation": "executor | driver | owner",
  "team_size_signal": "small | medium | large | unclear",
  "role_summary": "<2 sentences: what this person actually does day-to-day>",
  "interview_likely_focuses": ["system design", "leadership"],
  "compensation_signal": "below-market | market | above-market | equity-heavy | unclear"
}}
"""


async def run(state: dict) -> dict:
    log.info("jd_agent_start")
    try:
        raw = await call_model(PROMPT.format(jd_text=state["jd_text"]))
        signals = extract_json(raw)
        log.info("jd_agent_done", seniority=signals.get("seniority_level"))
        return {"jd_signals": signals}
    except Exception as e:
        log.error("jd_agent_failed", error=str(e))
        return {"jd_signals": None, "error": str(e)}
