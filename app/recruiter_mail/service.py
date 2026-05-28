import asyncio
import base64
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from openai import OpenAI
from app.core.models import call_model, extract_json
from app.db.client import get_db
from app.config import get_settings
from uuid import uuid4
import structlog

log = structlog.get_logger()

BATCH_SIZE = 5


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

FETCH_AND_CLASSIFY_PROMPT = """
Fetch the last {max_emails} emails from the past {days_back} days from Gmail.
For each email, classify whether it is from a recruiter, HR, headhunter, or talent acquisition team.

Return ONLY a valid JSON array with no markdown:
[
  {{
    "gmail_id": "<gmail message id>",
    "subject": "<email subject>",
    "sender": "<sender email>",
    "received_at": "<ISO 8601 date or null>",
    "body": "<first 400 chars of email body>",
    "is_recruiter": true or false,
    "company_name": "<company or null>",
    "role_title": "<job title or null>",
    "intent": "inbound_opportunity | follow_up | rejection | interview_invite | other",
    "urgency": "low | medium | high",
    "summary": "<2-line summary>",
    "application_status": "applied | interview_scheduled | rejected | verification | unknown"
  }}
]

Only include emails where is_recruiter is true.
Skip no-reply, noreply, do-not-reply senders entirely.
Return empty array [] if no recruiter emails found.
"""

REPLY_PROMPT = """
You are helping a job candidate reply to this recruiter email.

EMAIL:
Subject: {subject}
From: {sender}
Body: {body}

Write 3 reply options. Return ONLY valid JSON:
{{
  "drafts": [
    {{"tone": "professional", "body": "<reply text>"}},
    {{"tone": "friendly",     "body": "<reply text>"}},
    {{"tone": "brief",        "body": "<reply text>"}}
  ]
}}
"""

SEND_REPLY_PROMPT = """
Send a reply email using Gmail with these details:

TO: {to}
SUBJECT: {subject}
THREAD ID: {thread_id}
BODY:
{body}

Send this as a reply in the existing thread.
"""


# ---------------------------------------------------------------------------
# Groq Gmail MCP client
# ---------------------------------------------------------------------------

def _get_groq_mcp_client() -> OpenAI:
    return OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=get_settings().groq_api_key,
    )

def _get_gmail_mcp_tool(access_token: str) -> dict:
    return {
        "type": "mcp",
        "server_label": "Gmail",
        "connector_id": "connector_gmail",
        "authorization": access_token,
        "require_approval": "never",
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_already_scanned_ids(user_id: str) -> set[str]:
    try:
        result = (
            get_db().table("recruiter_emails")
            .select("gmail_id")
            .eq("user_id", user_id)
            .execute()
        )
        return {row["gmail_id"] for row in (result.data or [])}
    except Exception as e:
        log.warning("already_scanned_fetch_failed", error=str(e))
        return set()


def _save_recruiter_email(user_id: str, email: dict) -> dict | None:
    record_id = str(uuid4())
    get_db().table("recruiter_emails").upsert(
        {
            "id":                 record_id,
            "user_id":            user_id,
            "gmail_id":           email["gmail_id"],
            "subject":            email.get("subject"),
            "sender":             email.get("sender"),
            "received_at":        email.get("received_at"),
            "is_recruiter":       True,
            "company_name":       email.get("company_name"),
            "role_title":         email.get("role_title"),
            "intent":             email.get("intent"),
            "urgency":            email.get("urgency"),
            "summary":            email.get("summary"),
            "reply_drafts":       [],
            "application_status": email.get("application_status"),
            "status":             "unread",
        },
        on_conflict="gmail_id",
    ).execute()

    log.info(
        "recruiter_email_saved",
        company=email.get("company_name"),
        intent=email.get("intent"),
    )
    return {**email, "reply_drafts": [], "id": record_id}


# ---------------------------------------------------------------------------
# Main scan entry point
# ---------------------------------------------------------------------------

async def scan_gmail_and_analyse(
    user_id: str,
    access_token: str,
    days_back: int = 30,
    max_emails: int = 50,
) -> list[dict]:
    """
    Single MCP call to Gmail:
    1. Fetch last N emails from past X days
    2. Classify recruiter emails — all in one shot
    3. Dedup against DB
    4. Save new recruiter emails
    """

    # 1. Already scanned IDs — skip re-processing
    already_scanned = _get_already_scanned_ids(user_id)
    log.info("scan_start", user_id=user_id, already_in_db=len(already_scanned))

    # 2. Single MCP call — fetch + classify together
    def _call_mcp() -> str:
        client = _get_groq_mcp_client()
        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=FETCH_AND_CLASSIFY_PROMPT.format(
                max_emails=max_emails,
                days_back=days_back,
            ),
            tools=[_get_gmail_mcp_tool(access_token)],
            temperature=0.1,
            top_p=0.4,
        )
        return response.output_text or ""

    try:
        raw = await asyncio.to_thread(_call_mcp)
        log.info("mcp_scan_done", chars=len(raw))
    except Exception as e:
        log.error("mcp_scan_failed", error=str(e))
        return []

    # 3. Parse classified emails
    try:
        emails = extract_json(raw)
        if not isinstance(emails, list):
            log.warning("mcp_bad_response_shape", raw=raw[:300])
            return []
        log.info("mcp_emails_classified", count=len(emails))
    except Exception as e:
        log.error("mcp_parse_failed", error=str(e))
        return []

    # 4. Dedup + save
    saved: list[dict] = []
    for email in emails:
        gmail_id = email.get("gmail_id")
        if not gmail_id:
            continue
        if gmail_id in already_scanned:
            log.info("email_already_scanned", gmail_id=gmail_id)
            continue
        if not email.get("is_recruiter"):
            continue

        try:
            result = _save_recruiter_email(user_id, email)
            if result:
                saved.append(result)
        except Exception as e:
            log.warning("email_save_failed", gmail_id=gmail_id, error=str(e))

    log.info(
        "scan_complete",
        newly_saved=len(saved),
        skipped_already_in_db=len(already_scanned),
    )
    return saved


# ---------------------------------------------------------------------------
# On-demand reply draft generation
# ---------------------------------------------------------------------------

async def generate_reply_drafts(email_id: str, user_id: str) -> list[dict]:
    """Generate reply drafts on demand and persist them."""
    try:
        result = (
            get_db().table("recruiter_emails")
            .select("*")
            .eq("id", email_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        log.error("email_fetch_failed", email_id=email_id, error=str(e))
        raise ValueError("Email not found.")

    email = result.data if result else None
    if not email:
        raise ValueError("Email not found.")

    sender = (email.get("sender") or "").lower()
    no_reply_tokens = ["no-reply", "noreply", "donotreply", "do-not-reply"]
    if any(t in sender for t in no_reply_tokens):
        raise ValueError("Cannot generate replies for automated/no-reply emails.")

    reply_raw = await call_model(
        REPLY_PROMPT.format(
            subject=email.get("subject", ""),
            sender=email.get("sender", ""),
            body=email.get("summary", "")[:500],
        )
    )
    drafts = extract_json(reply_raw).get("drafts", [])

    get_db().table("recruiter_emails") \
        .update({"reply_drafts": drafts}) \
        .eq("id", email_id) \
        .eq("user_id", user_id) \
        .execute()

    log.info("reply_drafts_generated", email_id=email_id, count=len(drafts))
    return drafts


# ---------------------------------------------------------------------------
# Send reply via Gmail MCP
# ---------------------------------------------------------------------------

async def send_reply(
    access_token: str,
    gmail_id: str,
    to: str,
    subject: str,
    body: str,
) -> dict:
    """Send a reply in the existing Gmail thread via MCP."""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    def _call_mcp() -> str:
        client = _get_groq_mcp_client()
        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=SEND_REPLY_PROMPT.format(
                to=to,
                subject=subject,
                thread_id=gmail_id,
                body=body,
            ),
            tools=[_get_gmail_mcp_tool(access_token)],
            temperature=0.1,
            top_p=0.4,
        )
        return response.output_text or ""

    try:
        result = await asyncio.to_thread(_call_mcp)
        log.info("reply_sent_via_mcp", to=to, gmail_id=gmail_id)
        return {"sent": True, "result": result}
    except Exception as e:
        log.error("mcp_send_reply_failed", error=str(e))
        raise ValueError(f"Failed to send reply: {e}")


# ---------------------------------------------------------------------------
# Misc CRUD
# ---------------------------------------------------------------------------

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
    get_db().table("recruiter_emails") \
        .update({"status": status}) \
        .eq("id", email_id) \
        .eq("user_id", user_id) \
        .execute()


async def update_status_by_gmail_id(gmail_id: str, user_id: str, status: str) -> None:
    try:
        get_db().table("recruiter_emails") \
            .update({"status": status}) \
            .eq("gmail_id", gmail_id) \
            .eq("user_id", user_id) \
            .execute()
    except Exception as e:
        log.warning("update_status_by_gmail_id_failed", error=str(e))