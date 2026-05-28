import asyncio
import re
from email.mime.text import MIMEText
import base64
from openai import OpenAI
from app.core.models import call_model, extract_json
from app.db.client import get_db
from app.config import get_settings
from uuid import uuid4
import structlog

log = structlog.get_logger()

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


# ── Groq + Exa MCP client ─────────────────────────────────────────────────────

def _get_groq_mcp_client() -> OpenAI:
    return OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=get_settings().groq_api_key,
    )

def _get_exa_mcp_tool() -> dict:
    return {
        "type": "mcp",
        "server_url": f"https://mcp.exa.ai/mcp?exaApiKey={get_settings().exa_api_key}",
        "server_label": "exa",
        "require_approval": "never",
    }


# ── Step 1: DB cache check ────────────────────────────────────────────────────

async def get_cached_contacts(company_name: str) -> dict | None:
    try:
        result = (
            get_db().table("company_contacts")
            .select("*")
            .ilike("company_name", company_name.strip())
            .limit(1)
            .execute()
        )
        data = result.data
        if data:
            get_db().table("company_contacts") \
                .update({"search_count": data[0]["search_count"] + 1}) \
                .eq("id", data[0]["id"]) \
                .execute()
            log.info("contacts_from_cache", company=company_name)
            return data[0]
        return None
    except Exception as e:
        log.warning("cache_check_failed", error=str(e))
        return None


async def save_contacts_to_cache(company_name: str, domain: str, emails: list[dict]) -> None:
    try:
        normalized = company_name.strip().lower()
        existing = (
            get_db().table("company_contacts")
            .select("id")
            .ilike("company_name", normalized)
            .limit(1)
            .execute()
        )
        if existing.data:
            get_db().table("company_contacts") \
                .update({"domain": domain, "emails": emails}) \
                .eq("id", existing.data[0]["id"]) \
                .execute()
        else:
            get_db().table("company_contacts").insert({
                "id": str(uuid4()),
                "company_name": company_name.strip(),
                "domain": domain,
                "emails": emails,
            }).execute()
        log.info("contacts_cached", company=company_name, count=len(emails))
    except Exception as e:
        log.warning("cache_save_failed", error=str(e))


# ── Step 2: Find domain + emails via Exa MCP (single call) ───────────────────

CONTACT_SEARCH_PROMPT = """
Find the official website domain AND recruiter/HR contact emails for this company.

COMPANY: {company_name}
ROLE: {role_title}

Instructions:
1. First find the official company website (NOT LinkedIn, Glassdoor, Indeed, ZoomInfo, Crunchbase)
2. Then find real recruiter or HR email addresses for this company
3. Search for: "{company_name} recruiter HR email careers contact"
4. Search the company's own careers page for contact emails
5. Only return emails that belong to the company's own domain

Return ONLY valid JSON:
{{
  "domain": "example.com",
  "emails": [
    {{
      "email": "recruiter@example.com",
      "type": "recruiter | hr | careers | generic",
      "confidence": <50-95>,
      "source": "exa"
    }}
  ]
}}

If no real emails found, return empty list for emails but still return the domain.
Never return emails from ZoomInfo, LinkedIn, Glassdoor, or any third-party site.
"""


async def _find_contacts_via_mcp(company_name: str, role_title: str) -> dict:
    """
    Single Exa MCP call to find both domain and recruiter emails.
    Returns {"domain": str, "emails": list[dict]}
    """
    def _call() -> str:
        client = _get_groq_mcp_client()
        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=CONTACT_SEARCH_PROMPT.format(
                company_name=company_name,
                role_title=role_title,
            ),
            tools=[_get_exa_mcp_tool()],
            temperature=0.1,
            top_p=0.4,
        )
        return response.output_text or ""

    try:
        raw = await asyncio.to_thread(_call)
        log.info("mcp_contact_search_done", company=company_name, chars=len(raw))
        data = extract_json(raw)
        domain = data.get("domain", "")
        emails = data.get("emails", [])

        # Safety filter — reject any email not on company domain
        if domain:
            emails = [e for e in emails if domain in e.get("email", "")]

        log.info("mcp_contacts_found", company=company_name,
                 domain=domain, count=len(emails))
        return {"domain": domain, "emails": emails}

    except Exception as e:
        log.error("mcp_contact_search_failed", error=str(e))
        return {"domain": "", "emails": []}


# ── Step 3: Generic fallback emails ──────────────────────────────────────────

def _generic_fallback_emails(domain: str) -> list[dict]:
    """Last resort — common HR email patterns for the domain."""
    if not domain:
        return []
    patterns = [
        f"careers@{domain}",
        f"recruiting@{domain}",
        f"hr@{domain}",
        f"jobs@{domain}",
        f"talent@{domain}",
    ]
    return [
        {"email": e, "type": "generic", "confidence": 30, "source": "pattern"}
        for e in patterns
    ]


# ── Main find_contacts orchestrator ──────────────────────────────────────────

async def find_contacts(company_name: str, role_title: str) -> dict:
    """
    Pipeline:
    1. Check DB cache
    2. Exa MCP — find domain + emails in one call
    3. Fallback to generic patterns if nothing found
    4. Cache results
    """
    # 1. Cache check
    cached = await get_cached_contacts(company_name)
    if cached:
        return {
            "company_name": company_name,
            "domain": cached.get("domain"),
            "emails": cached.get("emails", []),
            "from_cache": True,
        }

    # 2. Exa MCP search
    result = await _find_contacts_via_mcp(company_name, role_title)
    domain = result["domain"]
    emails = result["emails"]

    # 3. Generic fallback if MCP found nothing
    if not emails and domain:
        emails = _generic_fallback_emails(domain)

    # Sort by confidence
    emails.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    # 4. Cache
    if domain or emails:
        await save_contacts_to_cache(company_name, domain, emails)

    log.info("find_contacts_done",
             company=company_name, domain=domain, count=len(emails))
    return {
        "company_name": company_name,
        "domain": domain,
        "emails": emails,
        "from_cache": False,
    }


# ── Cold email generator ──────────────────────────────────────────────────────

GENERATE_PROMPT = """
You are an expert at writing cold emails that actually get replies from recruiters.

CANDIDATE PROFILE:
{candidate_profile}

TARGET:
Company: {company_name}
Role: {role_title}
Recipient email: {to_email}

Write 3 cold email variants. Each must:
- Be under 150 words
- Have a compelling subject line
- Feel human, not AI-generated
- End with a clear single call to action

Return ONLY valid JSON:
{{
  "variants": [
    {{
      "tone": "direct",
      "subject": "<subject line>",
      "body": "<email body>"
    }},
    {{
      "tone": "story",
      "subject": "<subject line>",
      "body": "<email body>"
    }},
    {{
      "tone": "value-prop",
      "subject": "<subject line>",
      "body": "<email body>"
    }}
  ]
}}

direct     → straight to the point, confident, no fluff
story      → opens with a relevant achievement or moment
value-prop → leads with what the candidate can do for the company
"""

FALLBACK_PROFILE = "Experienced software engineer seeking new opportunities."


async def generate_cold_email(
    company_name: str,
    to_email: str,
    role_title: str,
    analysis_id: str | None,
) -> dict:
    """Generate 3 cold email variants using the candidate's profile from DB."""
    candidate_profile = FALLBACK_PROFILE
    if analysis_id:
        try:
            result = (
                get_db().table("analyses")
                .select("candidate_profile, jd_signals")
                .eq("id", analysis_id)
                .limit(1)
                .execute()
            )
            if result.data and result.data[0].get("candidate_profile"):
                candidate_profile = str(result.data[0]["candidate_profile"])
        except Exception as e:
            log.warning("profile_fetch_failed", error=str(e))

    raw = await call_model(
        GENERATE_PROMPT.format(
            candidate_profile=candidate_profile,
            company_name=company_name,
            role_title=role_title,
            to_email=to_email,
        )
    )
    data = extract_json(raw)
    log.info("cold_email_generated",
             company=company_name, variants=len(data.get("variants", [])))
    return {
        "variants": data.get("variants", []),
        "company_name": company_name,
        "to_email": to_email,
    }


# ── Send cold email via Gmail ─────────────────────────────────────────────────

def _build_raw_email(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body, "plain")
    msg["To"] = to
    msg["Subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def _auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


async def send_cold_email(
    access_token: str,
    to_email: str,
    subject: str,
    body: str,
    company_name: str,
    user_id: str,
) -> dict:
    """Send the cold email via Gmail and save to cold_emails table."""
    import httpx
    raw = _build_raw_email(to_email, subject, body)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{GMAIL_API}/messages/send",
            json={"raw": raw},
            headers=_auth_headers(access_token),
        )

        if resp.status_code == 401:
            raise ValueError("Google token expired. User must re-login.")
        if resp.status_code == 403:
            raise ValueError("Gmail send scope not granted.")
        if not resp.is_success:
            log.error("cold_email_send_failed",
                      status=resp.status_code, body=resp.text)
            resp.raise_for_status()

        data = resp.json()
        gmail_msg_id = data.get("id")
        log.info("cold_email_sent",
                 to=to_email, company=company_name, id=gmail_msg_id)

    try:
        get_db().table("cold_emails").insert({
            "id": str(uuid4()),
            "user_id": user_id,
            "company_name": company_name,
            "to_email": to_email,
            "subject": subject,
            "body": body,
            "status": "sent",
            "gmail_msg_id": gmail_msg_id,
        }).execute()
    except Exception as e:
        log.warning("cold_email_save_failed", error=str(e))

    return {"sent": True, "gmail_message_id": gmail_msg_id}


async def get_cold_emails(user_id: str) -> list[dict]:
    """Return all cold emails sent by this user."""
    try:
        result = (
            get_db().table("cold_emails")
            .select("*")
            .eq("user_id", user_id)
            .order("sent_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        log.error("get_cold_emails_failed", error=str(e))
        return []