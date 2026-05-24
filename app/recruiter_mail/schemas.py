from pydantic import BaseModel
from typing import Optional


class EmailScanRequest(BaseModel):
    access_token: str
    days_back: int = 30
    max_emails: int = 20


class RecruiterEmailResponse(BaseModel):
    id: str
    gmail_id: str
    subject: Optional[str]
    sender: Optional[str]
    received_at: Optional[str]
    is_recruiter: bool
    company_name: Optional[str]
    role_title: Optional[str]
    intent: Optional[str]
    urgency: Optional[str]
    summary: Optional[str]
    reply_drafts: Optional[list]
    status: str


class StatusUpdate(BaseModel):
    status: str  # unread | replied | archived


class SendReplyRequest(BaseModel):
    access_token: str       # Google OAuth token
    gmail_id: str           # original email thread id to reply to
    body: str               # reply text
    subject: Optional[str]  # optional — auto-prefixed with Re: if not given
    to: str                 # recipient email address


class SendReplyResponse(BaseModel):
    sent: bool
    gmail_message_id: Optional[str]
    message: str