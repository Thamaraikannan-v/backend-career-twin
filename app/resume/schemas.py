from pydantic import BaseModel


class ResumeUploadResponse(BaseModel):
    resume_url: str
    char_count: int
    message: str = "Resume uploaded and parsed successfully"


class ParsedResume(BaseModel):
    text: str
    char_count: int
    page_count: int
