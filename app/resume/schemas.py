from pydantic import BaseModel
from typing import Optional


class ResumeUploadResponse(BaseModel):
    resume_url: str
    char_count: int
    message: str = "Resume uploaded and parsed successfully"


class ParsedResume(BaseModel):
    text: str
    char_count: int
    page_count: int


class SavedResumeResponse(BaseModel):
    """Metadata about the latest saved resume for a user."""
    has_resume: bool
    char_count: Optional[int] = None
    created_at: Optional[str] = None
    message: str = ""
