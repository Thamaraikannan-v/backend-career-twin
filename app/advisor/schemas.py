from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SkillGap(BaseModel):
    skill: str
    importance: int  # 1-10
    current_level: int  # 1-10
    required_level: int  # 1-10
    gap: int  # required - current


class RejectionPattern(BaseModel):
    reason: str
    frequency: int  # how many times this pattern appeared
    percentage: float  # % of rejections


class Milestone(BaseModel):
    week: int
    goal: str
    signal: str  # how to demonstrate progress
    resources: Optional[list[str]]


class TrajectoryAnalysis(BaseModel):
    current_level: str  # junior | mid | senior
    target_level: str
    estimated_weeks: int
    confidence: float  # 0-1
    rejection_patterns: list[RejectionPattern]
    critical_skills: list[SkillGap]
    milestones: list[Milestone]
    blockers: list[str]
    strengths: list[str]


class JobReadiness(BaseModel):
    job_title: str
    company: str
    match_score: int  # 0-100
    ready_now: bool
    ready_in_weeks: Optional[int]
    confidence: float  # 0-1
    remaining_gaps: list[str]
    next_steps: list[str]


class LearningResource(BaseModel):
    title: str
    type: str  # course | article | project | practice
    duration_hours: int
    difficulty: str  # beginner | intermediate | advanced
    skill_targets: list[str]
    why_this: str  # why this resource for you specifically


class LearningPlan(BaseModel):
    user_id: str
    target_role: str
    target_level: str
    estimated_weeks: int
    resources: list[LearningResource]
    projects_to_build: list[str]
    mock_interviews: list[str]


class ProgressUpdate(BaseModel):
    skill: str
    evidence: str  # what you did to prove progress
    level_before: int
    level_after: int
    timestamp: datetime


class CareerSnapshot(BaseModel):
    total_applications: int
    total_rejections: int
    rejection_rate: float
    top_rejection_reasons: list[RejectionPattern]
    applications_by_level: dict  # junior | mid | senior -> count
    avg_match_score: float
    trajectory_assessment: TrajectoryAnalysis