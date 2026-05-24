from fastapi import APIRouter, Depends, BackgroundTasks
from app.recruiter_mail import service
from app.recruiter_mail.schemas import EmailScanRequest, RecruiterEmailResponse, StatusUpdate
from app.dependencies import get_current_user, AuthUser

router = APIRouter(prefix="/api/recruiter-mail", tags=["recruiter-mail"])


@router.post("/scan")
async def scan_emails(
    body: EmailScanRequest,
    background_tasks: BackgroundTasks,
    user: AuthUser = Depends(get_current_user),
):
    """
    Trigger Gmail scan in the background.
    Frontend passes the Google OAuth access_token from the Supabase session.
    Returns immediately — poll GET /api/recruiter-mail/ for results.
    """
    background_tasks.add_task(
        service.scan_gmail_and_analyse,
        user_id=user.id,
        access_token=body.access_token,
        days_back=body.days_back,
        max_emails=body.max_emails,
    )
    return {
        "status": "scanning",
        "message": f"Scanning last {body.days_back} days of Gmail in background",
    }


@router.get("/", response_model=list[RecruiterEmailResponse])
async def list_emails(user: AuthUser = Depends(get_current_user)):
    """All detected recruiter emails for this user, newest first."""
    return await service.get_recruiter_emails(user.id)


@router.patch("/{email_id}/status")
async def update_status(
    email_id: str,
    body: StatusUpdate,
    user: AuthUser = Depends(get_current_user),
):
    """Mark email as replied or archived."""
    await service.update_email_status(email_id, user.id, body.status)
    return {"updated": True}