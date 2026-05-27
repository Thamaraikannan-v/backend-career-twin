import asyncio
import base64
import httpx
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from app.core.gemini import call_model, extract_json
from app.db.client import get_db
from uuid import uuid4
import structlog

log = structlog.get_logger()

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Batch classifier — accepts N emails, returns an array of N results.
# Each item index corresponds exactly to the input email index.
BATCH_CLASSIFIER_PROMPT = """
You are an expert at identifying recruiter emails.

Below are {count} emails numbered 0 to {last}.
Analyze ALL of them and return ONLY a valid JSON array with exactly {count} objects,
one per email in the same order, no extra keys, no markdown:

[
  {{
    "index": 0,
    "is_recruiter": true or false,
    "company_name": "<company or null>",
    "role_title": "<job title or null>",
    "intent": "inbound_opportunity | follow_up | rejection | interview_invite | other",
    "urgency": "low | medium | high",
    "summary": "<2-line summary>",
    "application_status": "applied | interview_scheduled | rejected | verification | unknown"
  }},
  ...
]

is_recruiter is true only if sent by a recruiter, headhunter, HR, or talent acquisition team.

EMAILS:
{emails_block}
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

# How many emails to classify in a single LLM call.
# Keep low enough so the prompt fits comfortably in context.
BATCH_SIZE = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
            cleaned = date_str.rsplit("(", 1)[0].strip()
            return parsedate_to_datetime(cleaned).isoformat()
        except Exception:
            log.warning("date_parse_failed", date=date_str)
            return None


def _is_no_reply_email(email: dict) -> bool:
    sender  = (email.get("sender")  or "").lower()
    subject = (email.get("subject") or "").lower()
    body    = (email.get("body")    or "").lower()
    tokens  = ["no-reply", "noreply", "do not reply", "donotreply", "do-not-reply", "no reply"]
    return any(t in sender or t in subject or t in body for t in tokens)


# ---------------------------------------------------------------------------
# Gmail API helpers
# ---------------------------------------------------------------------------

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
            "received_at": _parse_gmail_date(headers.get("Date", "")),
            "body":        data.get("snippet", ""),
        }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_already_scanned_ids(user_id: str) -> set[str]:
    """
    Return the set of gmail_ids already stored in the DB for this user.
    Used to skip re-scanning emails we've already classified.
    """
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


# ---------------------------------------------------------------------------
# Batch classification
# ---------------------------------------------------------------------------

def _build_emails_block(emails: list[dict]) -> str:
    """Format a list of email dicts into a numbered text block for the prompt."""
    lines = []
    for i, e in enumerate(emails):
        lines.append(
            f"--- EMAIL {i} ---\n"
            f"Subject: {e.get('subject', '')}\n"
            f"From: {e.get('sender', '')}\n"
            f"Body: {e.get('body', '')[:400]}\n"
        )
    return "\n".join(lines)


async def _classify_batch(emails: list[dict]) -> list[dict]:
    """
    Send a single LLM call for a batch of emails.
    Returns a list of classification dicts in the same order as `emails`.
    Falls back to empty dicts on parse failure so the caller can skip those.
    """
    if not emails:
        return []

    prompt = BATCH_CLASSIFIER_PROMPT.format(
        count=len(emails),
        last=len(emails) - 1,
        emails_block=_build_emails_block(emails),
    )

    try:
        raw = await call_model(prompt)
        results = extract_json(raw)

        # extract_json may return a dict with a wrapper key instead of a list
        if isinstance(results, dict):
            results = results.get("emails") or results.get("results") or list(results.values())[0]

        if not isinstance(results, list):
            log.warning("batch_classifier_bad_shape", raw=raw[:200])
            return [{} for _ in emails]

        # Build index → result map; fall back gracefully for missing indices
        indexed: dict[int, dict] = {}
        for item in results:
            if isinstance(item, dict) and "index" in item:
                indexed[item["index"]] = item

        return [indexed.get(i, {}) for i in range(len(emails))]

    except Exception as e:
        log.warning("batch_classify_failed", error=str(e), batch_size=len(emails))
        return [{} for _ in emails]


# ---------------------------------------------------------------------------
# Main scan entry point
# ---------------------------------------------------------------------------

async def scan_gmail_and_analyse(
    user_id: str,
    access_token: str,
    days_back: int = 30,
    max_emails: int = 50,
) -> list[dict]:
    # 1. Fetch Gmail message IDs
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

    # 2. Skip IDs that are already in the DB — no LLM tokens wasted
    already_scanned = _get_already_scanned_ids(user_id)
    new_ids = [mid for mid in ids if mid not in already_scanned]

    log.info(
        "scan_dedup",
        total_fetched=len(ids),
        already_in_db=len(already_scanned),
        to_classify=len(new_ids),
    )

    if not new_ids:
        log.info("scan_all_cached", message="All emails already classified, nothing to do.")
        return []

    # 3. Fetch email metadata concurrently
    fetch_tasks = [_gmail_get(access_token, mid) for mid in new_ids]
    fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    emails_to_classify: list[dict] = []
    for mid, res in zip(new_ids, fetch_results):
        if isinstance(res, Exception):
            log.warning("email_fetch_skipped", id=mid, error=str(res))
        else:
            emails_to_classify.append(res)

    if not emails_to_classify:
        return []

    # 4. Classify in batches of BATCH_SIZE — one LLM call per batch
    saved: list[dict] = []

    for batch_start in range(0, len(emails_to_classify), BATCH_SIZE):
        batch = emails_to_classify[batch_start : batch_start + BATCH_SIZE]
        log.info("classifying_batch", start=batch_start, size=len(batch))

        classifications = await _classify_batch(batch)

        for email, clf in zip(batch, classifications):
            if not clf:
                log.warning("email_skipped_no_clf", gmail_id=email.get("gmail_id"))
                continue
            if not clf.get("is_recruiter"):
                continue

            try:
                result = _save_recruiter_email(user_id, email, clf)
                if result:
                    saved.append(result)
            except Exception as e:
                log.warning("email_save_failed", gmail_id=email.get("gmail_id"), error=str(e))

    log.info(
        "scan_complete",
        total_fetched=len(ids),
        newly_classified=len(emails_to_classify),
        recruiter_emails_saved=len(saved),
    )
    return saved


# ---------------------------------------------------------------------------
# Persist a single classified recruiter email
# ---------------------------------------------------------------------------

def _save_recruiter_email(user_id: str, email: dict, clf: dict) -> dict | None:
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
            "company_name":       clf.get("company_name"),
            "role_title":         clf.get("role_title"),
            "intent":             clf.get("intent"),
            "urgency":            clf.get("urgency"),
            "summary":            clf.get("summary"),
            "reply_drafts":       [],
            "application_status": clf.get("application_status"),
            "status":             "unread",
        },
        on_conflict="gmail_id",
    ).execute()

    log.info(
        "recruiter_email_saved",
        company=clf.get("company_name"),
        intent=clf.get("intent"),
    )
    return {**clf, "reply_drafts": [], "id": record_id}


# ---------------------------------------------------------------------------
# On-demand reply draft generation (unchanged logic, kept here for reference)
# ---------------------------------------------------------------------------

async def generate_reply_drafts(email_id: str, user_id: str) -> list[dict]:
    """
    Generate reply drafts on demand for a single email and persist them.
    Called only when the user explicitly clicks "Generate Replies" in the UI.
    Raises ValueError for no-reply senders or if the email is not found.
    """
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
        log.error("email_fetch_failed", email_id=email_id, user_id=user_id, error=str(e))
        raise ValueError("Email not found.")

    email = result.data if result else None
    if not email:
        log.warning("email_not_found", email_id=email_id, user_id=user_id)
        raise ValueError("Email not found.")

    if _is_no_reply_email(email):
        raise ValueError("Cannot generate replies for automated/no-reply emails.")

    reply_raw = await call_model(
        REPLY_PROMPT.format(
            subject=email.get("subject", ""),
            sender=email.get("sender", ""),
            body=email.get("summary", "")[:500],
        )
    )
    drafts = extract_json(reply_raw).get("drafts", [])

    get_db().table("recruiter_emails")\
        .update({"reply_drafts": drafts})\
        .eq("id", email_id)\
        .eq("user_id", user_id)\
        .execute()

    log.info("reply_drafts_generated", email_id=email_id, count=len(drafts))
    return drafts


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
    get_db().table("recruiter_emails")\
        .update({"status": status})\
        .eq("id", email_id)\
        .eq("user_id", user_id)\
        .execute()


# ---------------------------------------------------------------------------
# Send reply
# ---------------------------------------------------------------------------

def _build_raw_email(to: str, subject: str, body: str, thread_id: str) -> str:
    msg = MIMEText(body, "plain")
    msg["To"] = to
    msg["Subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


async def _get_thread_id(access_token: str, message_id: str) -> str:
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
    gmail_id: str,
    to: str,
    subject: str,
    body: str,
) -> dict:
    log.info("sending_reply", gmail_id=gmail_id, to=to)

    try:
        thread_id = await _get_thread_id(access_token, gmail_id)
        log.info("thread_id_resolved", message_id=gmail_id, thread_id=thread_id)
    except Exception as e:
        log.warning("thread_id_fetch_failed_using_message_id", error=str(e))
        thread_id = gmail_id

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
    try:
        get_db().table("recruiter_emails")\
            .update({"status": status})\
            .eq("gmail_id", gmail_id)\
            .eq("user_id", user_id)\
            .execute()
    except Exception as e:
        log.warning("update_status_by_gmail_id_failed", error=str(e))