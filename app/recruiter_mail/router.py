from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from app.recruiter_mail import service
from app.recruiter_mail.schemas import (
    EmailScanRequest,
    RecruiterEmailResponse,
    ReplyDraftsResponse,
    StatusUpdate,
    SendReplyRequest,
    SendReplyResponse,
)
from app.dependencies import get_current_user, AuthUser

router = APIRouter(prefix="/api/recruiter-mail", tags=["recruiter-mail"])


@router.post("/scan")
async def scan_emails(
    body: EmailScanRequest,
    background_tasks: BackgroundTasks,
    user: AuthUser = Depends(get_current_user),
):
    """
    Trigger Gmail scan in background.
    Frontend passes Google OAuth access_token from Supabase session.
    Returns immediately — poll GET /api/recruiter-mail/ for results.
    Reply drafts are NOT generated here; use POST /{email_id}/drafts on demand.
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


@router.post("/{email_id}/drafts", response_model=ReplyDraftsResponse)
async def generate_drafts(
    email_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """
    Generate reply drafts for a single email on demand.
    Called when the user clicks "Generate Replies" in the frontend.
    Drafts are persisted to Supabase so subsequent GET / returns them.
    Returns 400 for no-reply/automated senders, 404 if email not found.
    """
    try:
        drafts = await service.generate_reply_drafts(email_id, user.id)
        return ReplyDraftsResponse(drafts=drafts)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate drafts: {e}")


@router.patch("/{email_id}/status")
async def update_status(
    email_id: str,
    body: StatusUpdate,
    user: AuthUser = Depends(get_current_user),
):
    """Mark email as replied or archived."""
    await service.update_email_status(email_id, user.id, body.status)
    return {"updated": True}


@router.post("/send", response_model=SendReplyResponse)
async def send_reply(
    body: SendReplyRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Send a reply to a recruiter email via Gmail.
    After sending, automatically marks the email as replied in Supabase.

    Requires gmail.send scope — make sure it's added to your Google OAuth config.
    """
    try:
        result = await service.send_reply(
            access_token=body.access_token,
            gmail_id=body.gmail_id,
            to=body.to,
            subject=body.subject or "",
            body=body.body,
        )

        # Auto-mark as replied in Supabase using gmail_id
        await service.update_status_by_gmail_id(
            gmail_id=body.gmail_id,
            user_id=user.id,
            status="replied",
        )

        return SendReplyResponse(
            sent=True,
            gmail_message_id=result.get("id"),
            message="Reply sent successfully",
        )

    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {e}")