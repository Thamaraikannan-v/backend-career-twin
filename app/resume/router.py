from fastapi import APIRouter, UploadFile, File, Depends
from app.resume.service import parse_pdf
from app.resume.schemas import ResumeUploadResponse
from app.dependencies import get_current_user, AuthUser
from app.core.exceptions import PDFParseError

router = APIRouter(prefix="/api/resume", tags=["resume"])


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
