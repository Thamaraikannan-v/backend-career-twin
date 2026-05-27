import os
from openai import OpenAI
from app.core.gemini import call_model, extract_json
from app.config import get_settings
import structlog

log = structlog.get_logger()

PROMPT = """
You are a recruitment intelligence analyst.
Based on the search results below, produce a hiring intelligence report for a job candidate.

COMPANY: {company_name}
JOB TITLE: {job_title}
ROLE LEVEL: {seniority_level}

Using live web search, research and provide:
1. Culture — what it is actually like to work there
2. Interview process — rounds, style, difficulty, what they test
3. Compensation — estimated base salary range for a {seniority_level} {job_title}
4. Hiring signals — actively hiring, on freeze, or expanding?
5. Reputation
6. Work-life balance signals
7. Remote / hybrid / onsite policy

Search for:
- "{company_name} {job_title} culture glassdoor review 2024 2025"
- "{company_name} {job_title} interview process"
- "{company_name} {job_title} salary levels.fyi compensation"
- "{company_name} hiring layoffs news 2025"

Return ONLY valid JSON:
{{
  "culture_summary": "<3-4 sentences of honest culture assessment>",
  "interview_style": "<what to expect in interviews>",
  "compensation_range": "<estimated range> only lpa",
  "hiring_signals": "<actively hiring, on freeze, or expanding?>",
  "red_flags": ["concern1"],
  "green_flags": ["positive1"],
  "wlb_signal": "poor | average | good | excellent",
  "remote_policy": "remote | hybrid | onsite | flexible",
  "company_stage": "early-startup | growth | late-stage | public | enterprise",
  "recent_news": "<1-2 sentences on latest relevant news>"
}}
"""

FALLBACK_PROMPT = """
You are a recruitment intelligence analyst.
Use your training knowledge to produce a hiring intelligence report for this company.

COMPANY: {company_name}
JOB TITLE: {job_title}
ROLE LEVEL: {seniority_level}

Provide your best knowledge on:
1. Culture
2. Interview process
3. Compensation estimates for {seniority_level} {job_title}
4. Hiring signals
5. Reputation
6. Work-life balance
7. Remote policy

Return ONLY valid JSON:
{{
  "culture_summary": "<3-4 sentences>",
  "interview_style": "<what to expect>",
  "compensation_range": "<estimated range> only lpa",
  "hiring_signals": "<hiring status>",
  "red_flags": ["concern1"],
  "green_flags": ["positive1"],
  "wlb_signal": "poor | average | good | excellent",
  "remote_policy": "remote | hybrid | onsite | flexible",
  "company_stage": "early-startup | growth | late-stage | public | enterprise",
  "recent_news": "<latest known news>"
}}
"""


def _get_groq_mcp_client() -> OpenAI:
    """Return an OpenAI-compatible Groq client."""
    return OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=get_settings().groq_api_key,
    )


async def _search_with_mcp(company_name: str, job_title: str, seniority: str) -> str:
    """
    Use Groq + Tavily MCP tool to search for company intel in a single call.
    The model autonomously decides what to search based on the prompt.
    Returns the model's response text (with search results baked in).
    Raises on failure so caller can fall back.
    """
    settings = get_settings()
    tavily_api_key = settings.tavily_api_key
    if not tavily_api_key:
        raise ValueError("TAVILY_API_KEY not set")

    client = _get_groq_mcp_client()

    tools = [
        {
            "type": "mcp",
            "server_url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_api_key}",
            "server_label": "tavily",
            "require_approval": "never",
        }
    ]

    prompt = PROMPT.format(
        company_name=company_name,
        job_title=job_title,
        seniority_level=seniority,
    )

    # Use client.responses.create (Groq's MCP-compatible endpoint)
    response = client.responses.create(
        model="openai/gpt-oss-120b",  # Groq's MCP-capable model
        input=prompt,
        tools=tools,
        temperature=0.1,
        top_p=0.4,
    )

    result = response.output_text
    if not result or not result.strip():
        raise ValueError("MCP search returned empty response")

    log.info("mcp_search_done", company=company_name, chars=len(result))
    return result


async def run(state: dict) -> dict:
    log.info("company_agent_start", company=state.get("company_name"))

    company_name = state["company_name"]
    seniority = "senior"
    job_title = "engineer"

    if state.get("jd_signals"):
        seniority = state["jd_signals"].get("seniority_level", "senior")
        job_title = state["jd_signals"].get("job_title", "engineer")

    # ── Step 1: Try MCP-powered search (Groq + Tavily MCP) ───────────────────
    mcp_result = None
    try:
        mcp_result = await _search_with_mcp(company_name, job_title, seniority)
    except Exception as e:
        log.warning("mcp_search_failed_falling_back", error=str(e))

    # ── Step 2: Extract JSON from MCP result, or fall back to plain LLM ───────
    if mcp_result:
        try:
            intel = extract_json(mcp_result)
            log.info("company_agent_done", company=company_name, source="groq+tavily-mcp")
            return {"company_intel": intel}
        except Exception as e:
            log.warning("mcp_json_extraction_failed_falling_back", error=str(e))

    # ── Step 3: Fallback — call your existing LLM (Groq/Gemini) without search ─
    try:
        fallback_prompt = FALLBACK_PROMPT.format(
            company_name=company_name,
            job_title=job_title,
            seniority_level=seniority,
        )
        raw = await call_model(fallback_prompt, use_search=False)
        intel = extract_json(raw)
        log.info("company_agent_done", company=company_name, source="llm-only-fallback")
        return {"company_intel": intel}

    except Exception as e:
        log.error("company_agent_failed", error=str(e))
        return {
            "company_intel": {
                "culture_summary": f"Could not retrieve data for {company_name}.",
                "interview_style": "Standard technical interviews expected.",
                "compensation_range": "Check Levels.fyi or Glassdoor for current ranges.",
                "hiring_signals": "Unknown — check LinkedIn for open roles.",
                "red_flags": [],
                "green_flags": [],
                "wlb_signal": "unknown",
                "remote_policy": "unknown",
                "company_stage": "unknown",
                "recent_news": "Unknown",
            }
        }