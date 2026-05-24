from app.core.gemini import call_model, extract_json
import structlog

log = structlog.get_logger()

PROMPT = """
You are a strategic resume consultant who has helped 500+ engineers land roles
at top companies. You understand what recruiters actually look for.

Rewrite this resume strategically to maximise the candidate's chance at
{company_name} for this specific role. Never invent experience they don't have.

─── ORIGINAL RESUME ─────────────────────────────────────────
{resume_text}

─── RECRUITER'S CONCERNS (address these) ────────────────────
{recruiter_simulation}

─── ROLE PRIORITIES (optimise for these) ────────────────────
{jd_signals}

─── COMPANY CULTURE (align tone to this) ────────────────────
{company_intel}

Return ONLY valid JSON:
{{
  "rewritten_summary": "<3-4 sentence professional summary tailored to this role>",
  "rewritten_experience": [
    {{
      "original_bullet": "<exact original bullet>",
      "rewritten_bullet": "<improved version>",
      "why": "<1 sentence: what changed and why>"
    }}
  ],
  "skills_to_emphasise": ["skill1", "skill2"],
  "skills_to_deprioritise": ["skill1"],
  "strategic_changes_summary": "<paragraph explaining overall rewrite strategy>",
  "keywords_added": ["keyword1"],
  "tone_shift": "<e.g. from implementation-focused to ownership-focused>"
}}
"""


async def run(state: dict) -> dict:
    log.info("rewrite_agent_start")
    try:
        raw = await call_model(
            PROMPT.format(
                company_name=state["company_name"],
                resume_text=state["resume_text"],
                recruiter_simulation=state.get("recruiter_simulation", {}),
                jd_signals=state.get("jd_signals", {}),
                company_intel=state.get("company_intel", {}),
            )
        )
        rewrite = extract_json(raw)
        log.info("rewrite_agent_done")
        return {"resume_rewrite": rewrite}
    except Exception as e:
        log.error("rewrite_agent_failed", error=str(e))
        return {"resume_rewrite": None, "error": str(e)}
