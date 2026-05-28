from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


class JobSearchRequest(BaseModel):
    role: str
    location: str
    days_old: int = 7
    salary_min: Optional[int] = None
    company: Optional[str] = None


class JobListing(BaseModel):
    id: str
    title: str
    company: str
    location: str
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = "USD"
    salary_lpa: Optional[str] = None          # ← human-readable LPA string
    job_type: Optional[str] = None
    posted_at: Optional[datetime] = None
    posted_label: Optional[str] = None        # ← "Recently posted" instead of fake date
    apply_url: Optional[str] = None
    description_snippet: Optional[str] = None
    source: str
    is_remote: bool = False
    match_score: Optional[int] = None
    match_reason: Optional[str] = None
    created_at: Optional[datetime] = None

    @field_validator("posted_at", mode="before")
    @classmethod
    def reject_old_dates(cls, v):
        """Null out any date before 2025 — LLM is hallucinating stale dates."""
        if v is None:
            return None
        try:
            if isinstance(v, str):
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            else:
                dt = v
            # Reject dates before 2025 as hallucinated
            if dt.year < 2025:
                return None
            return dt
        except Exception:
            return None

    @field_validator("salary_lpa", mode="before")
    @classmethod
    def compute_salary_lpa(cls, v):
        return v  # handled in model_validator below

    from pydantic import model_validator

    @model_validator(mode="after")
    def build_display_fields(self) -> "JobListing":
        # Build salary_lpa from salary_min/max
        if self.salary_min or self.salary_max:
            currency = self.salary_currency or "INR"
            if currency == "INR":
                # Convert raw rupees → LPA (divide by 100000)
                def to_lpa(val):
                    if not val:
                        return None
                    lpa = val / 100_000
                    return f"{lpa:.0f} LPA" if lpa == int(lpa) else f"{lpa:.1f} LPA"

                lo = to_lpa(self.salary_min)
                hi = to_lpa(self.salary_max)
                if lo and hi and lo != hi:
                    self.salary_lpa = f"₹{lo} – ₹{hi}"
                elif lo:
                    self.salary_lpa = f"₹{lo}"
            else:
                # USD / GBP — keep as-is in k
                def to_k(val):
                    return f"{val // 1000}k" if val and val >= 1000 else str(val)

                lo = to_k(self.salary_min)
                hi = to_k(self.salary_max)
                symbol = "$" if currency == "USD" else "£"
                if lo and hi and lo != hi:
                    self.salary_lpa = f"{symbol}{lo} – {symbol}{hi}"
                elif lo:
                    self.salary_lpa = f"{symbol}{lo}"

        # Build posted_label
        if self.posted_at:
            delta = datetime.utcnow() - self.posted_at.replace(tzinfo=None)
            if delta.days == 0:
                self.posted_label = "Today"
            elif delta.days == 1:
                self.posted_label = "Yesterday"
            elif delta.days < 7:
                self.posted_label = f"{delta.days} days ago"
            elif delta.days < 30:
                weeks = delta.days // 7
                self.posted_label = f"{weeks} week{'s' if weeks > 1 else ''} ago"
            else:
                self.posted_label = self.posted_at.strftime("%b %d, %Y")
        else:
            self.posted_label = "Recently posted"

        return self


class JobSearchResponse(BaseModel):
    search_id: str
    query: str
    jobs: list[JobListing]
    total_count: int
    from_cache: bool


class SaveJobRequest(BaseModel):
    job_listing_id: str
    status: str


class SaveJobResponse(BaseModel):
    saved: bool
    message: str


class JobSearchHistory(BaseModel):
    id: str
    role: str
    location: str
    days_old: int
    result_count: int
    searched_at: datetime