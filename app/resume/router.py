from fastapi import APIRouter, UploadFile, File, Depends
from app.resume.service import parse_pdf
from app.resume.schemas import ResumeUploadResponse, SavedResumeResponse
from app.analysis import service as analysis_service
from app.dependencies import get_current_user, AuthUser
from app.core.exceptions import PDFParseError

router = APIRouter(prefix="/api/resume", tags=["resume"])


@router.get("/saved", response_model=SavedResumeResponse)
async def check_saved_resume(user: AuthUser = Depends(get_current_user)):
    """
    Check if user has a saved resume in the database.
    Returns metadata (char_count, created_at) if available, or has_resume=False if none exists.
    Used by frontend to show "Reuse Resume" option or prompt for upload.
    """
    saved = await analysis_service.get_latest_resume_metadata_for_user(user.id)
    if not saved:
        return SavedResumeResponse(
            has_resume=False,
            message="No saved resume found. Please upload one.",
        )

    return SavedResumeResponse(
        has_resume=True,
        char_count=saved.get("resume_char_count"),
        created_at=saved.get("created_at"),
        message="Resume available for reuse.",
    )


@router.post("/upload", response_model=ResumeUploadResponse)
async def upload_resume(
    resume: UploadFile = File(..., description="Resume PDF file"),
    user: AuthUser = Depends(get_current_user),
):
    """
    Parse and validate a resume PDF.
    Used by the frontend to verify the file before submitting the full analysis.
    The actual upload to Supabase Storage happens inside analysis/service.py
    when the full analysis is triggered.
    """
    if not resume.filename.lower().endswith(".pdf"):
        raise PDFParseError("Only PDF files are accepted.")

    file_bytes = await resume.read()
    parsed = parse_pdf(file_bytes)

    return ResumeUploadResponse(
        resume_url="",          # populated after analysis starts
        char_count=parsed["char_count"],
    )
