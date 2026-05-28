import asyncio
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, Any
from app.agents import (
    resume_agent,
    jd_agent,
    company_agent,
    recruiter_agent,
    rewrite_agent,
    baseline_agent,
)
import structlog

log = structlog.get_logger()


# ── State ─────────────────────────────────────────────────────────────────────

class AnalysisState(TypedDict, total=False):
    # Inputs
    resume_text:  str
    jd_text:      str
    company_name: str
    user_id:      str
    analysis_id:  str

    # Agent outputs
    candidate_profile:    Optional[dict]
    jd_signals:           Optional[dict]
    company_intel:        Optional[dict]
    recruiter_simulation: Optional[dict]
    resume_rewrite:       Optional[Any]
    baseline_score:       Optional[float]
    keyword_analysis:     Optional[dict]

    # Control
    error: Optional[str]


# ── Node wrappers ─────────────────────────────────────────────────────────────

async def node_resume(state: AnalysisState) -> dict:
    try:
        return await resume_agent.run(state)
    except Exception as e:
        log.error("resume_agent_failed", error=str(e))
        return {"candidate_profile": {}, "error": str(e)}


async def node_jd(state: AnalysisState) -> dict:
    if not state.get("candidate_profile"):
        log.warning("jd_skipped_no_resume_profile")
        return {"jd_signals": {}}
    try:
        return await jd_agent.run(state)
    except Exception as e:
        log.error("jd_agent_failed", error=str(e))
        return {"jd_signals": {}}


async def node_parallel_research(state: AnalysisState) -> dict:
    """
    Runs company_agent (MCP search) and baseline_agent (embeddings)
    concurrently. Times out after 45s so a slow MCP call never blocks forever.
    """
    log.info("parallel_research_start")
    try:
        company_result, baseline_result = await asyncio.wait_for(
            asyncio.gather(
                company_agent.run(state),
                baseline_agent.run(state),
            ),
            timeout=45.0,
        )
    except asyncio.TimeoutError:
        log.warning("parallel_research_timeout")
        company_result  = {"company_intel": {}}
        baseline_result = {"baseline_score": 0.0, "keyword_analysis": {}}
    except Exception as e:
        log.error("parallel_research_failed", error=str(e))
        company_result  = {"company_intel": {}}
        baseline_result = {"baseline_score": 0.0, "keyword_analysis": {}}

    return {**company_result, **baseline_result}


async def node_recruiter_and_rewrite(state: AnalysisState) -> dict:
    """
    recruiter_sim and rewrite have no dependency on each other —
    run them concurrently to save ~2-4s.
    """
    log.info("recruiter_and_rewrite_start")
    try:
        recruiter_result, rewrite_result = await asyncio.wait_for(
            asyncio.gather(
                recruiter_agent.run(state),
                rewrite_agent.run(state),
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        log.warning("recruiter_and_rewrite_timeout")
        recruiter_result = {"recruiter_simulation": {}}
        rewrite_result   = {"resume_rewrite": {}}
    except Exception as e:
        log.error("recruiter_and_rewrite_failed", error=str(e))
        recruiter_result = {"recruiter_simulation": {}}
        rewrite_result   = {"resume_rewrite": {}}

    return {**recruiter_result, **rewrite_result}


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Pipeline:
      resume_intel
        → jd_intel
          → parallel_research   (company + baseline concurrently)
            → recruiter_and_rewrite  (recruiter_sim + rewrite concurrently)
    """
    g = StateGraph(AnalysisState)

    g.add_node("resume_intel",          node_resume)
    g.add_node("jd_intel",              node_jd)
    g.add_node("parallel_research",     node_parallel_research)
    g.add_node("recruiter_and_rewrite", node_recruiter_and_rewrite)

    g.set_entry_point("resume_intel")
    g.add_edge("resume_intel",          "jd_intel")
    g.add_edge("jd_intel",              "parallel_research")
    g.add_edge("parallel_research",     "recruiter_and_rewrite")
    g.add_edge("recruiter_and_rewrite", END)

    return g.compile()


# ── Singleton — compiled once at startup ─────────────────────────────────────

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph