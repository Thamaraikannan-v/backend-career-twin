"""
Improved baseline scorer using sentence-transformers (semantic) +
section-aware TF-IDF + exact keyword matching.
No LLM cost. Runs in parallel with company_agent.
"""
import re
import asyncio
from functools import lru_cache
import structlog
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer, util

log = structlog.get_logger()

# ── Model singleton (loaded once, reused) ─────────────────────────────────────
@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """
    Load once at startup, cache forever.
    'all-MiniLM-L6-v2' → 80MB, fast, good quality.
    Upgrade to 'all-mpnet-base-v2' for better accuracy (420MB, slower).
    """
    log.info("loading_sentence_transformer")
    return SentenceTransformer("all-MiniLM-L6-v2")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _clean(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_section(text: str, section_names: list[str]) -> str:
    """Pull a named section out of resume text."""
    pattern = "|".join(section_names)
    parts = re.split(rf"(?i)\b({pattern})\b", text)
    for i, part in enumerate(parts):
        if re.search(pattern, part, re.I) and i + 1 < len(parts):
            return parts[i + 1][:800]
    return ""


def _tfidf_sim(text_a: str, text_b: str) -> float:
    if not text_a.strip() or not text_b.strip():
        return 0.0
    try:
        vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", max_features=3000)
        mat = vec.fit_transform([_clean(text_a), _clean(text_b)])
        return float(cosine_similarity(mat[0:1], mat[1:2])[0][0])
    except Exception:
        return 0.0


def _semantic_sim(text_a: str, text_b: str) -> float:
    """Sentence-transformer cosine similarity — understands synonyms/paraphrasing."""
    if not text_a.strip() or not text_b.strip():
        return 0.0
    try:
        model = _get_model()
        emb_a, emb_b = model.encode([text_a, text_b], convert_to_tensor=True)
        return float(util.cos_sim(emb_a, emb_b))
    except Exception as e:
        log.warning("semantic_sim_failed", error=str(e))
        return 0.0


def _chunk_semantic_sim(resume_text: str, jd_text: str, chunk_size: int = 300) -> float:
    """
    Split resume into chunks, score each against full JD,
    return the MAX chunk score.
    
    Why: a 600-word resume encoded as one vector averages out
    everything. Chunking finds the BEST matching section.
    This is closer to how recruiter attention works.
    """
    words = resume_text.split()
    if len(words) <= chunk_size:
        return _semantic_sim(resume_text, jd_text)

    model = _get_model()
    chunks = [
        " ".join(words[i: i + chunk_size])
        for i in range(0, len(words), chunk_size // 2)  # 50% overlap
    ]
    jd_emb = model.encode(jd_text, convert_to_tensor=True)
    chunk_embs = model.encode(chunks, convert_to_tensor=True)
    scores = util.cos_sim(chunk_embs, jd_emb)
    return float(scores.max())


def _exact_keyword_hit(resume_text: str, jd_text: str) -> float:
    """Check what % of JD tech terms appear literally in the resume."""
    jd_terms = set(re.findall(
        r'\b[a-z][a-z0-9+#.]*(?:\s[a-z][a-z0-9+#.]*){0,2}\b',
        jd_text.lower()
    ))
    jd_terms = {t for t in jd_terms if len(t) > 4}
    if not jd_terms:
        return 0.0
    resume_lower = resume_text.lower()
    matched = sum(1 for t in jd_terms if t in resume_lower)
    return matched / len(jd_terms)


def _experience_score(resume_text: str, jd_text: str) -> float:
    jd_years = re.search(r'(\d+)\+?\s*years?', jd_text, re.I)
    if not jd_years:
        return 1.0
    required = int(jd_years.group(1))
    resume_years = re.findall(r'(\d+)\+?\s*years?', resume_text, re.I)
    if not resume_years:
        return 0.5
    return min(max(int(y) for y in resume_years) / required, 1.0)


def _keyword_analysis(resume_text: str, jd_text: str) -> dict:
    stop = {
        "the", "and", "for", "with", "this", "that", "are", "you", "your",
        "our", "will", "have", "has", "been", "able", "also", "both", "each",
        "from", "into", "more", "than", "their", "them", "they", "using", "work",
    }
    def get_kw(text: str) -> set[str]:
        words = re.findall(r'\b[a-z][a-z0-9+#.]{2,}\b', text.lower())
        return {w for w in words if w not in stop and len(w) > 3}

    resume_kw = get_kw(resume_text)
    jd_kw     = get_kw(jd_text)
    return {
        "matched_keywords": sorted(resume_kw & jd_kw)[:20],
        "missing_keywords": sorted(jd_kw - resume_kw)[:20],
        "match_count":      len(resume_kw & jd_kw),
        "total_jd_keywords": len(jd_kw),
    }


# ── Main entry ────────────────────────────────────────────────────────────────
async def run(state: dict) -> dict:
    log.info("baseline_agent_start")
    try:
        resume = state["resume_text"]
        jd     = state["jd_text"]

        # Run heavy operations in thread pool (they're CPU-bound, not async)
        loop = asyncio.get_event_loop()

        skills_text = _extract_section(resume, ["skills", "technical skills", "technologies"])
        exp_text    = _extract_section(resume, ["experience", "work experience", "employment"])

        (
            full_semantic,
            chunk_semantic,
            skills_semantic,
            skills_tfidf,
            exp_semantic,
            exact_hit,
            yoe_score,
        ) = await asyncio.gather(
            loop.run_in_executor(None, _semantic_sim, resume, jd),
            loop.run_in_executor(None, _chunk_semantic_sim, resume, jd),
            loop.run_in_executor(None, _semantic_sim, skills_text or resume, jd),
            loop.run_in_executor(None, _tfidf_sim, skills_text or resume, jd),
            loop.run_in_executor(None, _semantic_sim, exp_text or resume, jd),
            loop.run_in_executor(None, _exact_keyword_hit, resume, jd),
            loop.run_in_executor(None, _experience_score, resume, jd),
        )

        # ── Weighted combination ──────────────────────────────────────────────
        # chunk_semantic is the star — finds best matching section
        raw = (
            chunk_semantic   * 0.30 +
            skills_semantic  * 0.25 +
            exp_semantic     * 0.15 +
            full_semantic    * 0.10 +
            skills_tfidf     * 0.10 +
            exact_hit        * 0.07 +
            yoe_score        * 0.03
        )

        score = round(max(raw * 100, 15.0), 1)
        kw    = _keyword_analysis(resume, jd)

        log.info("baseline_agent_done", score=score, signals={
            "full_semantic":  round(full_semantic, 3),
            "chunk_semantic": round(chunk_semantic, 3),
            "skills_semantic":round(skills_semantic, 3),
            "skills_tfidf":   round(skills_tfidf, 3),
            "exp_semantic":   round(exp_semantic, 3),
            "exact_hit":      round(exact_hit, 3),
            "yoe_score":      round(yoe_score, 3),
        })

        return {"baseline_score": score, "keyword_analysis": kw}

    except Exception as e:
        log.error("baseline_agent_failed", error=str(e))
        return {"baseline_score": 0.0, "keyword_analysis": {}}