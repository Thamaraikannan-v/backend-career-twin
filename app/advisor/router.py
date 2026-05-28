from fastapi import APIRouter, Depends, HTTPException
from app.advisor import service
from app.advisor.schemas import (
    TrajectoryAnalysis,
    LearningPlan,
    JobReadiness,
    CareerSnapshot,
)
from app.dependencies import get_current_user, AuthUser

router = APIRouter(prefix="/api/trajectory", tags=["trajectory"])


@router.get("/analysis")
async def get_trajectory_analysis(user: AuthUser = Depends(get_current_user)):
    """
    Analyze rejection patterns and predict career trajectory.
    Shows:
    - Why you're being rejected (pattern analysis)
    - What skills you're missing
    - How long until you're job-ready (weeks estimate)
    - Confidence level in the prediction
    """
    try:
        rejections = await service.analyze_rejections(user.id)
        gaps = await service.analyze_skill_gaps(user.id, "mid")
        trajectory = await service.predict_trajectory(user.id)

        return {
            "rejection_patterns": rejections.get("patterns", []),
            "critical_skills": gaps.get("gaps", []),
            "strengths": rejections.get("strengths", []),
            "blockers": rejections.get("blockers", []),
            "current_level": rejections.get("current_level", "unknown"),
            "target_level": rejections.get("target_level", "mid"),
            "estimated_weeks": trajectory.get("estimated_weeks"),
            "confidence": trajectory.get("confidence"),
            "milestones": trajectory.get("milestones", []),
            "key_insight": rejections.get("key_insight", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")


@router.get("/learning-plan")
async def get_learning_plan(user: AuthUser = Depends(get_current_user)):
    """
    Get personalized learning plan to close skill gaps.
    Includes:
    - Curated resources (not generic links)
    - Projects to build (to signal readiness)
    - Mock interview topics (role-specific)
    """
    try:
        plan = await service.generate_learning_plan(user.id)
        return {
            "resources": plan.get("resources", []),
            "projects_to_build": plan.get("projects", []),
            "mock_interviews": plan.get("interviews", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plan generation failed: {e}")


@router.post("/job-readiness/{job_listing_id}")
async def check_job_readiness(
    job_listing_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """
    Check readiness for a specific job.
    Returns:
    - Match score (0-100)
    - Are you ready now?
    - When will you be ready (weeks)?
    - Confidence level
    - Exact next steps to get ready
    """
    try:
        readiness = await service.score_job_readiness(user.id, job_listing_id)
        return {
            "match_score": readiness.get("match_score", 0),
            "ready_now": readiness.get("ready_now", False),
            "ready_in_weeks": readiness.get("ready_in_weeks"),
            "confidence": readiness.get("confidence", 0),
            "remaining_gaps": readiness.get("gaps", []),
            "next_steps": readiness.get("how_to_get_ready", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Readiness check failed: {e}")


@router.get("/snapshot")
async def get_career_snapshot(user: AuthUser = Depends(get_current_user)):
    """
    Get complete career overview:
    - Total applications + rejection rate
    - Top rejection reasons
    - Average match score across searches
    - Your trajectory prediction
    """
    try:
        snapshot = await service.get_career_snapshot(user.id)
        return snapshot
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot generation failed: {e}")