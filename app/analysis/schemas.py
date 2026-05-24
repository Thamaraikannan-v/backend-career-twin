from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class AnalyzeRequest(BaseModel):
    jd_text:      str = Field(..., min_length=50)
    company_name: str = Field(..., min_length=2)


class AnalyzeStartResponse(BaseModel):
    analysis_id: str
    status: str = "running"


class RecruiterSimulation(BaseModel):
    rejection_reason: str
    strengths: list[str]
    concerns: list[str]


class AnalysisListItem(BaseModel):
    id: str
    status: str
    company_name: Optional[str]
    baseline_score: Optional[float]
    created_at: Optional[datetime]


class AnalysisResponse(BaseModel):
    id: str
    status: str
    company_name:         Optional[str]
    candidate_profile:    Optional[dict]
    jd_signals:           Optional[dict]
    company_intel:        Optional[dict]
    recruiter_simulation: Optional[RecruiterSimulation]
    resume_rewrite:       Optional[dict]
    keyword_analysis:     Optional[dict]
    baseline_score:       Optional[float]
    created_at:           Optional[datetime]
