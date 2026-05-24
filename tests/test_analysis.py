"""
Unit tests for analysis service logic (no real DB or LLM calls).

Run:  pytest tests/test_analysis.py -v
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.analysis import service
from app.core.exceptions import FreeTierLimitError


@pytest.mark.asyncio
async def test_free_tier_blocks_at_limit():
    """Users who have 3 done analyses and are not pro should be blocked."""
    with (
        patch("app.analysis.service.db.is_pro",                return_value=False) as mock_pro,
        patch("app.analysis.service.db.count_done_analyses",   return_value=3),
        patch("app.analysis.service.parse_pdf",                return_value={"text": "x", "char_count": 1, "page_count": 1}),
    ):
        with pytest.raises(FreeTierLimitError):
            await service.start_analysis(
                user_id="user-1",
                company_name="Stripe",
                jd_text="x" * 60,
                file_bytes=b"fake",
            )


@pytest.mark.asyncio
async def test_pro_user_bypasses_free_tier():
    """Pro users are never blocked regardless of analysis count."""
    with (
        patch("app.analysis.service.db.is_pro",              return_value=True),
        patch("app.analysis.service.parse_pdf",              return_value={"text": "resume text", "char_count": 100, "page_count": 1}),
        patch("app.analysis.service.db.create_analysis",     return_value="new-id"),
        patch("app.analysis.service.upload_to_storage",      return_value=""),
        patch("app.analysis.service.db.set_resume_url",      new_callable=AsyncMock),
    ):
        analysis_id, resume_text = await service.start_analysis(
            user_id="pro-user",
            company_name="Google",
            jd_text="x" * 60,
            file_bytes=b"fake",
        )
        assert analysis_id == "new-id"
        assert resume_text == "resume text"


@pytest.mark.asyncio
async def test_get_analysis_raises_404_when_not_found():
    from app.core.exceptions import AnalysisNotFoundError
    with patch("app.analysis.service.db.get_analysis", return_value=None):
        with pytest.raises(AnalysisNotFoundError):
            await service.get_analysis("missing-id", "user-1")


def test_baseline_score_range():
    """Baseline score should always be between 0 and 100."""
    from app.agents.baseline_agent import run
    import asyncio

    state = {
        "resume_text": "Python engineer with 5 years experience in backend systems.",
        "jd_text":     "Looking for a Python backend engineer with cloud experience.",
    }
    result = asyncio.run(run(state))
    assert 0.0 <= result["baseline_score"] <= 100.0
    assert "matched_keywords" in result["keyword_analysis"]
