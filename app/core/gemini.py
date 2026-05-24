"""
Unified LLM client — supports Gemini and Groq, switchable via LLM_PROVIDER in .env.

LLM_PROVIDER=groq    → Groq for everything (recommended)
LLM_PROVIDER=gemini  → Gemini for everything
LLM_PROVIDER=auto    → Groq (same as groq, search is handled by Tavily in company_agent)
"""
import google.generativeai as genai
from app.config import get_settings
import json
import re
import time
import structlog

log = structlog.get_logger()

_gemini_configured = False
_groq_client = None


def _get_groq():
    global _groq_client
    if _groq_client is None:
        try:
            from groq import Groq
            _groq_client = Groq(api_key=get_settings().groq_api_key)
        except ImportError:
            raise RuntimeError("groq package not installed. Run: pip install groq")
    return _groq_client


def _ensure_gemini():
    global _gemini_configured
    if not _gemini_configured:
        genai.configure(api_key=get_settings().gemini_api_key)
        _gemini_configured = True


async def call_model(prompt: str, use_search: bool = False) -> str:
    """
    Route to the correct provider based on LLM_PROVIDER env var.
    use_search param is ignored — web search is handled by Tavily in company_agent.
    """
    provider = get_settings().llm_provider.lower()

    if provider == "gemini":
        return await _call_gemini(prompt)
    else:
        # groq or auto → Groq
        return await _call_groq(prompt)


async def _call_gemini(prompt: str) -> str:
    _ensure_gemini()
    model = genai.GenerativeModel(
        model_name="gemini-2.5-pro",
        generation_config=genai.GenerationConfig(
            temperature=0.3,
            max_output_tokens=4096,
        ),
    )
    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            if hasattr(response, "candidates") and response.candidates:
                parts = response.candidates[0].content.parts
                return "".join(p.text for p in parts if hasattr(p, "text"))
            return response.text
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                wait = 30 * (attempt + 1)
                log.warning("gemini_rate_limit", attempt=attempt + 1, wait_secs=wait)
                time.sleep(wait)
            else:
                raise


async def _call_groq(prompt: str) -> str:
    client = _get_groq()
    settings = get_settings()
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=4096,
            )
            return response.choices[0].message.content
        except Exception as e:
            if ("rate" in str(e).lower() or "429" in str(e)) and attempt < 2:
                wait = 10 * (attempt + 1)
                log.warning("groq_rate_limit", attempt=attempt + 1, wait_secs=wait)
                time.sleep(wait)
            else:
                raise


def extract_json(text: str) -> dict:
    """Robustly extract JSON from model output — handles fences and prose."""
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as e:
            log.error("json_extraction_failed", error=str(e), snippet=text[:300])
            raise ValueError(f"Could not extract valid JSON: {e}")

    raise ValueError("No JSON object found in model output")