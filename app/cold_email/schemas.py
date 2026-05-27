from pydantic import BaseModel
from typing import Optional


class FindContactsRequest(BaseModel):
    company_name: str
    role_title: str = "Software Engineer"


class ContactEmail(BaseModel):
    email: str
    type: str           # hr | recruiter | careers | generic
    confidence: int     # 0-100
    source: str         # hunter | tavily | cache


class FindContactsResponse(BaseModel):
    company_name: str
    domain: Optional[str]
    emails: list[ContactEmail]
    from_cache: bool


class GenerateEmailRequest(BaseModel):
    company_name: str
    to_email: str
    role_title: str
    analysis_id: Optional[str] = None   # pull candidate profile from existing analysis


class EmailVariant(BaseModel):
    tone: str           # direct | story | value-prop
    subject: str
    body: str


class GenerateEmailResponse(BaseModel):
    variants: list[EmailVariant]
    company_name: str
    to_email: str


class SendColdEmailRequest(BaseModel):
    access_token: str
    to_email: str
    subject: str
    body: str
    company_name: str


class SendColdEmailResponse(BaseModel):
    sent: bool
    gmail_message_id: Optional[str]
    message: str