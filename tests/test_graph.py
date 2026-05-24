"""
Smoke test — runs the full LangGraph pipeline with a fake resume.
Requires GEMINI_API_KEY in .env.

Run:  pytest tests/test_graph.py -v -s
"""
import pytest
from app.graph.graph import build_graph

FAKE_RESUME = """
John Doe | john@example.com | github.com/johndoe

EXPERIENCE
Senior Software Engineer — Acme Corp (2021–2024)
- Led migration of monolith to microservices, reducing p99 latency by 40%
- Owned the payments service processing $2M daily transactions
- Mentored 3 junior engineers, ran weekly code review sessions

Software Engineer — StartupXYZ (2019–2021)
- Built real-time analytics pipeline using Kafka + Flink
- Reduced infrastructure costs by 30% through right-sizing EC2 fleet

SKILLS: Python, Go, Kubernetes, PostgreSQL, Kafka, AWS, Redis

EDUCATION: BSc Computer Science — State University (2019)
"""

FAKE_JD = """
Senior Software Engineer — Platform Team

We're looking for a senior engineer to own critical backend services
and lead technical decisions across our platform.

Requirements:
- 5+ years backend experience
- Distributed systems and Kubernetes
- Python or Go
- Startup mindset — high ownership, fast delivery

Nice to have: event streaming (Kafka), fintech/payments experience
"""


@pytest.mark.asyncio
async def test_full_graph_runs():
    graph = build_graph()

    result = await graph.ainvoke({
        "resume_text":  FAKE_RESUME,
        "jd_text":      FAKE_JD,
        "company_name": "Stripe",
        "user_id":      "test-user-123",
        "analysis_id":  "test-analysis-456",
    })

    assert result.get("candidate_profile")    is not None, "resume_agent failed"
    assert result.get("jd_signals")           is not None, "jd_agent failed"
    assert result.get("company_intel")        is not None, "company_agent failed"
    assert result.get("recruiter_simulation") is not None, "recruiter_agent failed"
    assert result.get("resume_rewrite")       is not None, "rewrite_agent failed"
    assert result.get("baseline_score")       is not None, "baseline_agent failed"

    sim = result["recruiter_simulation"]
    assert 0 <= sim["hiring_confidence"] <= 100

    print("\n── Graph smoke test results ─────────────────────")
    print(f"  Hiring confidence : {sim['hiring_confidence']}")
    print(f"  Rejection reason  : {sim.get('likely_rejection_reason')}")
    print(f"  Baseline score    : {result['baseline_score']}")
    print(f"  Culture fit       : {sim.get('culture_fit_score')}")
