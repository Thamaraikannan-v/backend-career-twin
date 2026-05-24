import httpx
from email.utils import parsedate_to_datetime
from app.core.gemini import call_model, extract_json
from app.db.client import get_db
from uuid import uuid4
import structlog

log = structlog.get_logger()

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

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
  "summary": "<detailed 2 line summary of the email>"
}}

is_recruiter is true if sent by a recruiter, headhunter, HR, or talent acquisition team.
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


def _auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _parse_gmail_date(date_str: str) -> str | None:
    """
    Convert Gmail date header to ISO 8601 for Postgres.
    Gmail sends: "Sun, 24 May 2026 01:55:16 +0000 (UTC)"
    Postgres wants: "2026-05-24T01:55:16+00:00"
    """
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).isoformat()
    except Exception:
        try:
            # Strip timezone label like "(UTC)" and retry
            cleaned = date_str.rsplit("(", 1)[0].strip()
            return parsedate_to_datetime(cleaned).isoformat()
        except Exception:
            log.warning("date_parse_failed", date=date_str)
            return None


async def _gmail_list(access_token: str, days_back: int, max_emails: int) -> list[str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{GMAIL_API}/messages",
            params={"q": f"newer_than:{days_back}d", "maxResults": max_emails},
            headers=_auth_headers(access_token),
        )
        log.info("gmail_list_status", status=resp.status_code)

        if resp.status_code == 401:
            raise ValueError("Google token expired or missing Gmail scope. User must re-login.")
        if resp.status_code == 403:
            raise ValueError("Gmail API not enabled or scope not granted.")

        resp.raise_for_status()
        data = resp.json()
        ids = [m["id"] for m in data.get("messages", [])]
        log.info("gmail_ids_fetched", count=len(ids))
        return ids


async def _gmail_get(access_token: str, msg_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{GMAIL_API}/messages/{msg_id}",
            params={"format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"]},
            headers=_auth_headers(access_token),
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
            "received_at": _parse_gmail_date(headers.get("Date", "")),  # ← parsed here
            "body":        data.get("snippet", ""),
        }


def _is_no_reply_email(email: dict) -> bool:
    """
    Heuristic to detect automated/no-reply emails. Returns True when the
    sender, subject or snippet/body contains common no-reply tokens.
    """
    sender = (email.get("sender") or "").lower()
    subject = (email.get("subject") or "").lower()
    body = (email.get("body") or "").lower()
    tokens = [
        "no-reply",
        "noreply",
        "do not reply",
        "donotreply",
        "do-not-reply",
        "no reply",
    ]
    for t in tokens:
        if t in sender or t in subject or t in body:
            return True
    return False


async def scan_gmail_and_analyse(
    user_id: str,
    access_token: str,
    days_back: int = 30,
    max_emails: int = 50,
) -> list[dict]:
    try:
        ids = await _gmail_list(access_token, days_back, max_emails)
    except ValueError as e:
        log.error("gmail_auth_error", error=str(e))
        return []
    except Exception as e:
        log.error("gmail_list_failed", error=str(e))
        return []

    if not ids:
        log.warning("gmail_empty", hint="Check token has gmail.readonly scope")
        return []

    saved = []
    for msg_id in ids:
        try:
            email = await _gmail_get(access_token, msg_id)
            result = await _classify_and_save(user_id, email)
            if result:
                saved.append(result)
        except Exception as e:
            log.warning("email_skipped", id=msg_id, error=str(e))

    log.info("scan_complete", total=len(ids), recruiter_emails=len(saved))
    return saved


async def _classify_and_save(user_id: str, email: dict) -> dict | None:
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

    drafts = []
    # Do not generate reply drafts for automated / no-reply senders
    if _is_no_reply_email(email):
        log.info("skipping_drafts_for_no_reply_email", gmail_id=email.get("gmail_id"), sender=email.get("sender"))
        drafts = []
    else:
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

    record_id = str(uuid4())
    get_db().table("recruiter_emails").upsert({
        "id":           record_id,
        "user_id":      user_id,
        "gmail_id":     email["gmail_id"],
        "subject":      email.get("subject"),
        "sender":       email.get("sender"),
        "received_at":  email.get("received_at"),   # now ISO 8601
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


# ── Send reply ────────────────────────────────────────────────────────────────

import base64
from email.mime.text import MIMEText


def _build_raw_email(to: str, subject: str, body: str, thread_id: str) -> str:
    """
    Build a base64url-encoded RFC 2822 email for the Gmail API.
    """
    msg = MIMEText(body, "plain")
    msg["To"] = to
    msg["Subject"] = subject
    # Gmail threads replies by matching subject, not In-Reply-To header
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw


async def _get_thread_id(access_token: str, message_id: str) -> str:
    """
    Gmail message id != thread id.
    Fetch the message to get the real threadId before replying.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{GMAIL_API}/messages/{message_id}",
            params={"format": "minimal"},
            headers=_auth_headers(access_token),
        )
        resp.raise_for_status()
        return resp.json().get("threadId", message_id)


async def send_reply(
    access_token: str,
    gmail_id: str,       # message id stored in Supabase
    to: str,
    subject: str,
    body: str,
) -> dict:
    """
    Send a reply email via Gmail REST API.
    Fetches the real threadId first so Gmail can thread the reply correctly.
    """
    log.info("sending_reply", gmail_id=gmail_id, to=to)

    # Step 1: resolve real thread id from message id
    try:
        thread_id = await _get_thread_id(access_token, gmail_id)
        log.info("thread_id_resolved", message_id=gmail_id, thread_id=thread_id)
    except Exception as e:
        log.warning("thread_id_fetch_failed_using_message_id", error=str(e))
        thread_id = gmail_id   # fallback — may not thread correctly but will still send

    # Step 2: prefix subject
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    raw = _build_raw_email(to, subject, body, thread_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{GMAIL_API}/messages/send",
            json={"raw": raw, "threadId": thread_id},
            headers=_auth_headers(access_token),
        )

        if resp.status_code == 401:
            raise ValueError("Google token expired. User must re-login.")
        if resp.status_code == 403:
            raise ValueError("Gmail send scope not granted. Need gmail.send scope.")

        if not resp.is_success:
            log.error("gmail_send_failed", body=resp.text, status=resp.status_code)
            resp.raise_for_status()

        data = resp.json()
        log.info("reply_sent", to=to, gmail_message_id=data.get("id"), thread_id=thread_id)
        return data


async def update_status_by_gmail_id(gmail_id: str, user_id: str, status: str) -> None:
    """Update status using gmail_id instead of Supabase row id."""
    try:
        get_db().table("recruiter_emails")\
            .update({"status": status})\
            .eq("gmail_id", gmail_id)\
            .eq("user_id", user_id)\
            .execute()
    except Exception as e:
        log.warning("update_status_by_gmail_id_failed", error=str(e))