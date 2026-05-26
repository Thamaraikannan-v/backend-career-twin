import httpx
from app.core.gemini import call_model, extract_json
from app.config import get_settings
import structlog

log = structlog.get_logger()

TAVILY_URL = "https://api.tavily.com/search"

PROMPT = """
You are a recruitment intelligence analyst.
Based on the search results below, produce a hiring intelligence report for a job candidate.

COMPANY: {company_name}
JOB TITLE: {job_title}
ROLE LEVEL: {seniority_level}

LIVE SEARCH RESULTS:
{search_results}

Using the search results above, provide:
1. Culture — what it is actually like to work there
2. Interview process — rounds, style, difficulty, what they test
3. Compensation — estimated base salary range for a {seniority_level} {job_title}
4. Hiring signals — actively hiring, on freeze, or expanding?
5. Reputation
6. Work-life balance signals
7. Remote / hybrid / onsite policy

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

# Fallback prompt when Tavily search fails — uses LLM training knowledge only
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


async def _tavily_search(company_name: str, job_title: str, seniority: str) -> str:
    """
    Search Tavily for company intel.
    Returns a formatted string of results to inject into the prompt.
    Raises on failure so caller can fall back.
    """
    api_key = get_settings().tavily_api_key
    if not api_key:
        raise ValueError("TAVILY_API_KEY not set")

    queries = [
        f"{company_name} {job_title} culture glassdoor review 2024 2025",
        f"{company_name} {job_title} interview process",
        f"{company_name} {job_title} salary levels.fyi compensation",
        f"{company_name} hiring layoffs news 2025",
    ]

    results_text = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        for query in queries:
            try:
                resp = await client.post(
                    TAVILY_URL,
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 3,
                        "include_answer": True,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                # Include Tavily's synthesised answer if present
                if data.get("answer"):
                    results_text.append(f"Q: {query}\nA: {data['answer']}")

                # Include top result snippets
                for r in data.get("results", [])[:2]:
                    snippet = r.get("content", "")[:400]
                    if snippet:
                        results_text.append(f"Source: {r.get('url','')}\n{snippet}")

            except Exception as e:
                log.warning("tavily_query_failed", query=query[:50], error=str(e))
                continue

    if not results_text:
        raise ValueError("Tavily returned no results")

    return "\n\n---\n\n".join(results_text)


async def run(state: dict) -> dict:
    log.info("company_agent_start", company=state.get("company_name"))

    company_name = state["company_name"]
    seniority = "senior"
    job_title = "engineer"  # fallback
    
    if state.get("jd_signals"):
        seniority = state["jd_signals"].get("seniority_level", "senior")
        job_title = state["jd_signals"].get("job_title", "engineer")

    # ── Step 1: Try Tavily search ─────────────────────────────────────────────
    search_results = None
    try:
        search_results = await _tavily_search(company_name, job_title, seniority)
        log.info("tavily_search_done", company=company_name, chars=len(search_results))
    except Exception as e:
        log.warning("tavily_failed_using_llm_knowledge", error=str(e))

    # ── Step 2: Build prompt (with or without search results) ─────────────────
    if search_results:
        prompt = PROMPT.format(
            company_name=company_name,
            job_title=job_title,
            seniority_level=seniority,
            search_results=search_results,
        )
    else:
        prompt = FALLBACK_PROMPT.format(
            company_name=company_name,
            job_title=job_title,
            seniority_level=seniority,
        )

    # ── Step 3: Call LLM (Groq or Gemini based on LLM_PROVIDER) ──────────────
    try:
        raw = await call_model(prompt, use_search=False)  # search already done by Tavily
        intel = extract_json(raw)
        log.info("company_agent_done",
                 company=company_name,
                 source="tavily+llm" if search_results else "llm-only")
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