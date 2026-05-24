import httpx
from app.core.gemini import call_model, extract_json
from app.db.client import get_db
from uuid import uuid4
import structlog

log = structlog.get_logger()

GMAIL_MCP_URL = "https://gmailmcp.googleapis.com/mcp/v1"

CLASSIFIER_PROMPT = """
You are an expert at identifying recruiter emails.

EMAIL:
Subject: {subject}
From: {sender}
Body: {body}

Analyze this email and return ONLY valid JSON:
{{
  "is_recruiter": true or false,
  "company_name": "<company or null>",
  "role_title": "<job title or null>",
  "intent": "inbound_opportunity | follow_up | rejection | interview_invite | other",
  "urgency": "low | medium | high",
  "summary": "<1 sentence summary of the email>"
}}

is_recruiter is true if sent by a recruiter, headhunter, HR, or talent acquisition team.
"""

REPLY_PROMPT = """
You are helping a job candidate reply to this recruiter email.

EMAIL:
Subject: {subject}
From: {sender}
Body: {body}

Write 3 reply options with different tones. Return ONLY valid JSON:
{{
  "drafts": [
    {{"tone": "professional", "body": "<reply text>"}},
    {{"tone": "friendly",     "body": "<reply text>"}},
    {{"tone": "brief",        "body": "<reply text>"}}
  ]
}}

Each reply should be concise and genuine. Do not mention AI.
"""


async def _gmail_list(access_token: str, days_back: int, max_emails: int) -> list[str]:
    """Fetch list of email IDs from Gmail MCP."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            GMAIL_MCP_URL,
            json={
                "method": "gmail.messages.list",
                "params": {
                    "q": f"newer_than:{days_back}d",
                    "maxResults": max_emails,
                },
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("messages", [])]


async def _gmail_get(access_token: str, msg_id: str) -> dict:
    """Fetch a single email's metadata + snippet."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            GMAIL_MCP_URL,
            json={
                "method": "gmail.messages.get",
                "params": {
                    "id": msg_id,
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        headers = {
            h["name"]: h["value"]
            for h in data.get("payload", {}).get("headers", [])
        }
        return {
            "gmail_id":    msg_id,
            "subject":     headers.get("Subject", ""),
            "sender":      headers.get("From", ""),
            "received_at": headers.get("Date", ""),
            "body":        data.get("snippet", ""),
        }


async def scan_gmail_and_analyse(
    user_id: str,
    access_token: str,       # Google OAuth token from frontend session
    days_back: int = 30,
    max_emails: int = 50,
) -> list[dict]:
    """
    Full pipeline — completely free, no Anthropic SDK:
    1. Gmail MCP via httpx  (no cost)
    2. Groq classifies each email  (free tier)
    3. Groq drafts replies  (free tier)
    4. Results saved to Supabase
    """
    # 1. Fetch email IDs
    try:
        ids = await _gmail_list(access_token, days_back, max_emails)
        log.info("gmail_ids_fetched", count=len(ids))
    except Exception as e:
        log.error("gmail_list_failed", error=str(e))
        return []

    # 2. Fetch each email and process
    saved = []
    for msg_id in ids:
        try:
            email = await _gmail_get(access_token, msg_id)
            result = await _classify_and_save(user_id, email)
            if result:
                saved.append(result)
        except Exception as e:
            log.warning("email_skipped", id=msg_id, error=str(e))

    log.info("scan_complete", recruiter_emails=len(saved))
    return saved


async def _classify_and_save(user_id: str, email: dict) -> dict | None:
    """
    Classify one email with Groq.
    If recruiter, generate reply drafts and save to Supabase.
    """
    # Classify (Groq — free)
    raw = await call_model(
        CLASSIFIER_PROMPT.format(
            subject=email.get("subject", ""),
            sender=email.get("sender", ""),
            body=email.get("body", "")[:500],
        )
    )
    clf = extract_json(raw)

    if not clf.get("is_recruiter"):
        return None

    # Generate reply drafts (Groq — free)
    drafts = []
    try:
        reply_raw = await call_model(
            REPLY_PROMPT.format(
                subject=email.get("subject", ""),
                sender=email.get("sender", ""),
                body=email.get("body", "")[:500],
            )
        )
        drafts = extract_json(reply_raw).get("drafts", [])
    except Exception as e:
        log.warning("reply_draft_failed", error=str(e))

    # Save to Supabase (upsert on gmail_id avoids duplicates on re-scan)
    record_id = str(uuid4())
    get_db().table("recruiter_emails").upsert({
        "id":           record_id,
        "user_id":      user_id,
        "gmail_id":     email["gmail_id"],
        "subject":      email.get("subject"),
        "sender":       email.get("sender"),
        "received_at":  email.get("received_at"),
        "is_recruiter": True,
        "company_name": clf.get("company_name"),
        "role_title":   clf.get("role_title"),
        "intent":       clf.get("intent"),
        "urgency":      clf.get("urgency"),
        "summary":      clf.get("summary"),
        "reply_drafts": drafts,
        "status":       "unread",
    }, on_conflict="gmail_id").execute()

    log.info("recruiter_email_saved",
             company=clf.get("company_name"),
             intent=clf.get("intent"))

    return {**clf, "reply_drafts": drafts, "id": record_id}


async def get_recruiter_emails(user_id: str) -> list[dict]:
    result = (
        get_db().table("recruiter_emails")
        .select("*")
        .eq("user_id", user_id)
        .order("received_at", desc=True)
        .execute()
    )
    return result.data or []


async def update_email_status(email_id: str, user_id: str, status: str) -> None:
    get_db().table("recruiter_emails")\
        .update({"status": status})\
        .eq("id", email_id)\
        .eq("user_id", user_id)\
        .execute()