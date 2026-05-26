import httpx
import re
from email.mime.text import MIMEText
import base64
from app.core.gemini import call_model, extract_json
from app.db.client import get_db
from app.config import get_settings
from uuid import uuid4
import structlog

log = structlog.get_logger()

TAVILY_URL  = "https://api.tavily.com/search"
HUNTER_URL  = "https://api.hunter.io/v2"
GMAIL_API   = "https://gmail.googleapis.com/gmail/v1/users/me"


# ── Step 1: DB cache check ────────────────────────────────────────────────────

async def get_cached_contacts(company_name: str) -> dict | None:
    """Return cached emails if we already searched this company before."""
    try:
        result = (
            get_db().table("company_contacts")
            .select("*")
            .ilike("company_name", company_name.strip())
            .limit(1)
            .execute()
        )
        data = result.data
        if data and len(data) > 0:
            # Bump search count
            get_db().table("company_contacts")\
                .update({"search_count": data[0]["search_count"] + 1})\
                .eq("id", data[0]["id"])\
                .execute()
            log.info("contacts_from_cache", company=company_name)
            return data[0]
        return None
    except Exception as e:
        log.warning("cache_check_failed", error=str(e))
        return None


async def save_contacts_to_cache(company_name: str, domain: str, emails: list[dict]) -> None:
    """Save found emails to DB for future users."""
    try:
        normalized = company_name.strip().lower()
        # Check if already exists first
        existing = get_db().table("company_contacts")            .select("id")            .ilike("company_name", normalized)            .limit(1)            .execute()

        if existing.data and len(existing.data) > 0:
            # Update existing record
            get_db().table("company_contacts")                .update({"domain": domain, "emails": emails})                .eq("id", existing.data[0]["id"])                .execute()
        else:
            # Insert new record
            get_db().table("company_contacts").insert({
                "id":           str(uuid4()),
                "company_name": company_name.strip(),
                "domain":       domain,
                "emails":       emails,
            }).execute()
        log.info("contacts_cached", company=company_name, count=len(emails))
    except Exception as e:
        log.warning("cache_save_failed", error=str(e))


# ── Step 2: Find company domain ───────────────────────────────────────────────

# Domains to skip — job boards, aggregators, social networks
SKIP_DOMAINS = {
    "linkedin.com", "in.linkedin.com", "glassdoor.com", "indeed.com",
    "crunchbase.com", "wikipedia.org", "bloomberg.com", "twitter.com",
    "facebook.com", "instagram.com", "youtube.com", "ambitionbox.com",
    "naukri.com", "monster.com", "shine.com", "timesjobs.com",
    "google.com", "bing.com", "yahoo.com",
}


async def _find_domain_via_tavily(company_name: str) -> str | None:
    """
    Search Tavily to find the company official domain.
    Skips job boards, LinkedIn, and aggregators.
    Tries multiple queries to get the real company site.
    """
    settings = get_settings()
    if not settings.tavily_api_key:
        return None

    # Multiple queries — first hit on a non-skip domain wins
    queries = [
        f"{company_name} official website",
        f"{company_name} company homepage careers",
        f"{company_name} Inc official site",
    ]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for query in queries:
                try:
                    resp = await client.post(
                        TAVILY_URL,
                        json={
                            "api_key":      settings.tavily_api_key,
                            "query":        query,
                            "search_depth": "basic",
                            "max_results":  5,
                            "include_answer": True,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    for result in data.get("results", []):
                        url = result.get("url", "")
                        match = re.search(
                            r"https?://(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", url
                        )
                        if not match:
                            continue
                        domain = match.group(1).lower()

                        # Strip subdomains like in.linkedin.com → linkedin.com
                        parts = domain.split(".")
                        if len(parts) > 2:
                            root = ".".join(parts[-2:])
                        else:
                            root = domain

                        if root in SKIP_DOMAINS or domain in SKIP_DOMAINS:
                            continue

                        log.info("domain_found", company=company_name, domain=domain)
                        return domain
                except Exception:
                    continue
    except Exception as e:
        log.warning("domain_search_failed", error=str(e))
    return None


# ── Step 3a: Hunter.io email finder ──────────────────────────────────────────

async def _search_hunter(domain: str, role_title: str) -> list[dict]:
    """
    Use Hunter.io domain search to find recruiter/HR emails.
    Free tier: 25 searches/month.
    """
    settings = get_settings()
    if not settings.hunter_api_key:
        log.warning("hunter_api_key_not_set")
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{HUNTER_URL}/domain-search",
                params={
                    "domain":   domain,
                    "api_key":  settings.hunter_api_key,
                    "type":     "personal",
                    "limit":    10,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            emails = []
            for entry in data.get("data", {}).get("emails", []):
                email = entry.get("value", "")
                if not email:
                    continue

                # Prioritise HR/recruiting roles
                position = (entry.get("position") or "").lower()
                dept      = (entry.get("department") or "").lower()
                is_hr     = any(k in position + dept for k in
                                ["recruit", "hr", "talent", "hiring", "people"])

                emails.append({
                    "email":      email,
                    "type":       "recruiter" if is_hr else "generic",
                    "confidence": entry.get("confidence", 50),
                    "source":     "hunter",
                })

            # Sort by HR relevance first, then confidence
            emails.sort(key=lambda x: (x["type"] == "recruiter", x["confidence"]), reverse=True)
            log.info("hunter_results", domain=domain, count=len(emails))
            return emails[:5]

    except Exception as e:
        log.warning("hunter_search_failed", error=str(e))
        return []


# ── Step 3b: Tavily email search fallback ─────────────────────────────────────

async def _search_emails_via_tavily(company_name: str, domain: str) -> list[dict]:
    """
    Fallback when Hunter.io finds nothing.
    Searches for publicly listed HR/careers emails.
    """
    settings = get_settings()
    if not settings.tavily_api_key:
        return []

    queries = [
        f"{company_name} recruiter HR email contact careers",
        f"site:{domain} careers email recruiting contact",
        f"{company_name} talent acquisition email address",
    ]

    found_emails = set()
    results = []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for query in queries:
                try:
                    resp = await client.post(
                        TAVILY_URL,
                        json={
                            "api_key":      settings.tavily_api_key,
                            "query":        query,
                            "search_depth": "basic",
                            "max_results":  3,
                            "include_answer": True,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    # Extract emails from snippets using regex
                    text = data.get("answer", "") + " ".join(
                        r.get("content", "") for r in data.get("results", [])
                    )
                    emails_found = re.findall(
                        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text
                    )
                    for email in emails_found:
                        email = email.lower()
                        if email in found_emails:
                            continue
                        if domain and domain not in email:
                            continue    # only keep emails from company domain
                        found_emails.add(email)

                        email_type = "generic"
                        if any(k in email for k in ["recruit", "hr", "talent", "hiring", "career", "jobs"]):
                            email_type = "careers"

                        results.append({
                            "email":      email,
                            "type":       email_type,
                            "confidence": 70 if email_type == "careers" else 50,
                            "source":     "tavily",
                        })
                except Exception:
                    continue
    except Exception as e:
        log.warning("tavily_email_search_failed", error=str(e))

    log.info("tavily_email_results", company=company_name, count=len(results))
    return results[:5]


# ── Step 3c: Generic fallback emails ─────────────────────────────────────────

def _generic_fallback_emails(domain: str) -> list[dict]:
    """
    If all else fails, return common HR email patterns for the domain.
    Low confidence but better than nothing.
    """
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
    Full pipeline:
    1. Check DB cache
    2. Find domain via Tavily
    3. Search Hunter.io for emails
    4. Fallback to Tavily email search
    5. Fallback to generic patterns
    6. Cache results
    """
    # 1. Cache check
    cached = await get_cached_contacts(company_name)
    if cached:
        return {
            "company_name": company_name,
            "domain":       cached.get("domain"),
            "emails":       cached.get("emails", []),
            "from_cache":   True,
        }

    # 2. Find domain
    domain = await _find_domain_via_tavily(company_name) or ""

    # 3. Hunter.io
    emails = await _search_hunter(domain, role_title) if domain else []

    # 4. Tavily fallback
    if len(emails) < 2:
        tavily_emails = await _search_emails_via_tavily(company_name, domain)
        # Merge, avoiding duplicates
        existing = {e["email"] for e in emails}
        for e in tavily_emails:
            if e["email"] not in existing:
                emails.append(e)
                existing.add(e["email"])

    # 5. Generic pattern fallback
    if not emails and domain:
        emails = _generic_fallback_emails(domain)

    # Sort by confidence
    emails.sort(key=lambda x: x["confidence"], reverse=True)

    # 6. Cache for future users
    if emails:
        await save_contacts_to_cache(company_name, domain, emails)

    log.info("find_contacts_done",
             company=company_name, domain=domain, count=len(emails))
    return {
        "company_name": company_name,
        "domain":       domain,
        "emails":       emails,
        "from_cache":   False,
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

direct    → straight to the point, confident, no fluff
story     → opens with a relevant achievement or moment
value-prop → leads with what the candidate can do for the company
"""

FALLBACK_PROFILE = """
Experienced software engineer seeking new opportunities.
Strong technical background with multiple years of experience.
"""


async def generate_cold_email(
    company_name: str,
    to_email: str,
    role_title: str,
    analysis_id: str | None,
) -> dict:
    """
    Generate 3 cold email variants using the candidate's profile from DB.
    """
    # Pull candidate profile from most recent analysis if available
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
    log.info("cold_email_generated", company=company_name, variants=len(data.get("variants", [])))
    return {
        "variants":     data.get("variants", []),
        "company_name": company_name,
        "to_email":     to_email,
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
        "Content-Type":  "application/json",
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
            log.error("cold_email_send_failed", status=resp.status_code, body=resp.text)
            resp.raise_for_status()

        data = resp.json()
        gmail_msg_id = data.get("id")
        log.info("cold_email_sent", to=to_email, company=company_name, id=gmail_msg_id)

    # Save to cold_emails table
    try:
        get_db().table("cold_emails").insert({
            "id":            str(uuid4()),
            "user_id":       user_id,
            "company_name":  company_name,
            "to_email":      to_email,
            "subject":       subject,
            "body":          body,
            "status":        "sent",
            "gmail_msg_id":  gmail_msg_id,
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