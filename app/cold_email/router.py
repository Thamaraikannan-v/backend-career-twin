from fastapi import APIRouter, Depends, HTTPException
from app.cold_email import service
from app.cold_email.schemas import (
    FindContactsRequest,
    FindContactsResponse,
    GenerateEmailRequest,
    GenerateEmailResponse,
    SendColdEmailRequest,
    SendColdEmailResponse,
)
from app.dependencies import get_current_user, AuthUser

router = APIRouter(prefix="/api/cold-email", tags=["cold-email"])


@router.post("/find-contacts", response_model=FindContactsResponse)
async def find_contacts(
    body: FindContactsRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Find recruiter/HR emails for a company.
    Checks DB cache first — runs Tavily + Hunter.io only if not cached.
    Cached results load instantly for future users.
    """
    result = await service.find_contacts(
        company_name=body.company_name,
        role_title=body.role_title,
    )
    return result


@router.post("/generate", response_model=GenerateEmailResponse)
async def generate_email(
    body: GenerateEmailRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Generate 3 cold email variants using the candidate's resume profile.
    Pass analysis_id to personalise based on a previous Career Twin analysis.
    """
    result = await service.generate_cold_email(
        company_name=body.company_name,
        to_email=body.to_email,
        role_title=body.role_title,
        analysis_id=body.analysis_id,
    )
    return result


@router.post("/send", response_model=SendColdEmailResponse)
async def send_cold_email(
    body: SendColdEmailRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Send the cold email via Gmail and save to outbox.
    Requires gmail.send scope in the Google OAuth token.
    """
    try:
        result = await service.send_cold_email(
            access_token=body.access_token,
            to_email=body.to_email,
            subject=body.subject,
            body=body.body,
            company_name=body.company_name,
            user_id=user.id,
        )
        return SendColdEmailResponse(
            sent=True,
            gmail_message_id=result.get("gmail_message_id"),
            message="Cold email sent successfully",
        )
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {e}")


@router.get("/outbox")
async def get_outbox(user: AuthUser = Depends(get_current_user)):
    """All cold emails sent by the current user."""
    return await service.get_cold_emails(user.id)