from fastapi import APIRouter, UploadFile, File, Form, Depends, BackgroundTasks
from app.analysis import service
from app.analysis.schemas import AnalyzeStartResponse, AnalysisResponse, AnalysisListItem
from app.dependencies import get_current_user, AuthUser
from app.core.exceptions import PDFParseError

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analyze", response_model=AnalyzeStartResponse)
async def analyze(
    background_tasks: BackgroundTasks,
    jd_text:         str        = Form(..., min_length=50),
    company_name:    str        = Form(..., min_length=2),
    use_saved_resume: bool      = Form(False, description="If True, use latest saved resume; if False, requires resume file"),
    resume:          UploadFile | None = File(None, description="Resume PDF (required if use_saved_resume=False)"),
    user: AuthUser = Depends(get_current_user),
):
    """
    Start an analysis.
    1. Validates free tier limit.
    2. Gets resume (from upload or DB if use_saved_resume=True).
    3. Creates DB row immediately (status=running).
    4. Kicks off LangGraph pipeline in the background.
    5. Returns analysis_id — frontend polls GET /api/analysis/:id.
    
    Args:
        use_saved_resume: If True, fetch latest resume from DB; if False, requires resume file upload.
    """
    file_bytes = None
    resume_text = None

    if use_saved_resume:
        # Fetch latest resume from DB
        latest = await service.get_latest_resume_for_user(user.id)
        if not latest:
            raise PDFParseError("No saved resume found. Please upload a resume first.")
        resume_text = latest.get("resume_text")
        if not resume_text:
            raise PDFParseError("Saved resume text is empty or corrupted.")
    else:
        # Require fresh resume upload
        if not resume:
            raise PDFParseError("Resume file is required when use_saved_resume=False.")
        if not resume.filename.lower().endswith(".pdf"):
            raise PDFParseError("Only PDF files are accepted.")
        file_bytes = await resume.read()

    # start_analysis handles free tier + PDF parse + DB row creation
    analysis_id, parsed_resume_text = await service.start_analysis(
        user_id=user.id,
        company_name=company_name,
        jd_text=jd_text,
        file_bytes=file_bytes,
        resume_text=resume_text,
    )

    # Run graph in background — response returns immediately
    background_tasks.add_task(
        service.run_graph_and_persist,
        analysis_id=analysis_id,
        resume_text=parsed_resume_text,
        jd_text=jd_text,
        company_name=company_name,
        user_id=user.id,
    )

    return AnalyzeStartResponse(analysis_id=analysis_id)


@router.get("/analysis/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """
    Poll for analysis results.
    Frontend polls every 3s until status == 'done' or 'failed'.
    """
    return await service.get_analysis(analysis_id, user.id)


@router.get("/analyses", response_model=list[AnalysisListItem])
async def list_analyses(user: AuthUser = Depends(get_current_user)):
    """All analyses for the current user, newest first."""
    return await service.list_analyses(user.id)
