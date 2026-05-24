from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class EmailScanRequest(BaseModel):
    access_token: str           # Google OAuth token from frontend
    days_back: int = 30
    max_emails: int = 50


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
    status: str                 # unread | replied | archived