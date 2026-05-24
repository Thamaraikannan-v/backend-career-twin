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
import asyncio
import structlog

log = structlog.get_logger()


# ── State ────────────────────────────────────────────────────────────────────

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
    return await resume_agent.run(state)


async def node_jd(state: AnalysisState) -> dict:
    return await jd_agent.run(state)


async def node_parallel_research(state: AnalysisState) -> dict:
    """
    Runs company_agent (LLM + Google Search) and baseline_agent (pure Python)
    concurrently using asyncio.gather — saves ~20-30s vs sequential.
    """
    log.info("parallel_research_start")
    company_result, baseline_result = await asyncio.gather(
        company_agent.run(state),
        baseline_agent.run(state),
    )
    return {**company_result, **baseline_result}


async def node_recruiter(state: AnalysisState) -> dict:
    return await recruiter_agent.run(state)


async def node_rewrite(state: AnalysisState) -> dict:
    return await rewrite_agent.run(state)


# ── Graph ────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Pipeline:
      resume_intel
        → jd_intel
          → parallel_research  (company + baseline concurrently)
            → recruiter_sim
              → rewrite
    """
    g = StateGraph(AnalysisState)

    g.add_node("resume_intel",      node_resume)
    g.add_node("jd_intel",          node_jd)
    g.add_node("parallel_research", node_parallel_research)
    g.add_node("recruiter_sim",     node_recruiter)
    g.add_node("rewrite",           node_rewrite)

    g.set_entry_point("resume_intel")
    g.add_edge("resume_intel",      "jd_intel")
    g.add_edge("jd_intel",          "parallel_research")
    g.add_edge("parallel_research", "recruiter_sim")
    g.add_edge("recruiter_sim",     "rewrite")
    g.add_edge("rewrite",           END)

    return g.compile()


# Singleton — compile once at startup via lifespan
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
