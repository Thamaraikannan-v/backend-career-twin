from fastapi import APIRouter, Depends, HTTPException
from app.job_search import service
from app.job_search.schemas import (
    JobSearchRequest,
    JobSearchResponse,
    SaveJobRequest,
    SaveJobResponse,
    JobSearchHistory,
)
from app.dependencies import get_current_user, AuthUser

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/search", response_model=JobSearchResponse)
async def search_jobs(
    body: JobSearchRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Search for jobs using Exa semantic search + LinkedIn.
    Caches results for 6 hours.
    Scores each job against user's candidate profile.
    
    Query breakdown:
    - role: job title (e.g. "Backend Engineer", "Data Scientist")
    - location: city or "Remote" (e.g. "Chennai", "Remote", "San Francisco")
    - days_old: posted in last N days (default 7)
    - salary_min: filter jobs with minimum salary
    - company: optional filter by specific company
    """
    result = await service.search_jobs(
        user_id=user.id,
        role=body.role,
        location=body.location,
        days_old=body.days_old,
        salary_min=body.salary_min,
        company=body.company,
    )
    return result


@router.post("/save", response_model=SaveJobResponse)
async def save_job(
    body: SaveJobRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Save or update a job's status.
    Status can be: saved | applied | interviewing | rejected | interested
    """
    success = await service.save_job(
        user_id=user.id,
        job_listing_id=body.job_listing_id,
        status=body.status,
    )
    if success:
        return SaveJobResponse(
            saved=True,
            message=f"Job status updated to '{body.status}'",
        )
    raise HTTPException(status_code=500, detail="Failed to save job")


@router.get("/saved")
async def get_saved_jobs(user: AuthUser = Depends(get_current_user)):
    """
    Get all user's saved/applied/interviewing jobs.
    """
    jobs = await service.get_saved_jobs(user.id)
    return {"count": len(jobs), "jobs": jobs}


@router.get("/history", response_model=list[JobSearchHistory])
async def get_search_history(user: AuthUser = Depends(get_current_user)):
    """
    Get user's past job searches (up to 20 most recent).
    """
    history = await service.get_search_history(user.id)
    return history