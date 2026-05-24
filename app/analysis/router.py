from fastapi import APIRouter, UploadFile, File, Form, Depends, BackgroundTasks
from app.analysis import service
from app.analysis.schemas import AnalyzeStartResponse, AnalysisResponse, AnalysisListItem
from app.dependencies import get_current_user, AuthUser
from app.core.exceptions import PDFParseError

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analyze", response_model=AnalyzeStartResponse)
async def analyze(
    background_tasks: BackgroundTasks,
    resume:       UploadFile = File(..., description="Resume PDF"),
    jd_text:      str        = Form(..., min_length=50),
    company_name: str        = Form(..., min_length=2),
    user: AuthUser = Depends(get_current_user),
):
    """
    Start an analysis.
    1. Validates free tier limit.
    2. Parses PDF.
    3. Creates DB row immediately (status=running).
    4. Kicks off LangGraph pipeline in the background.
    5. Returns analysis_id — frontend polls GET /api/analysis/:id.
    """
    if not resume.filename.lower().endswith(".pdf"):
        raise PDFParseError("Only PDF files are accepted.")

    file_bytes = await resume.read()

    # start_analysis handles free tier + PDF parse + DB row creation
    analysis_id, resume_text = await service.start_analysis(
        user_id=user.id,
        company_name=company_name,
        jd_text=jd_text,
        file_bytes=file_bytes,
    )

    # Run graph in background — response returns immediately
    background_tasks.add_task(
        service.run_graph_and_persist,
        analysis_id=analysis_id,
        resume_text=resume_text,
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
