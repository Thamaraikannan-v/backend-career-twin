"""
Non-AI baseline scorer using TF-IDF cosine similarity.
Runs in parallel with company_agent — pure Python, no LLM cost.
Used in the UI to show AI vs rule-based comparison.
"""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import re
import structlog

log = structlog.get_logger()


def _clean(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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


async def run(state: dict) -> dict:
    log.info("baseline_agent_start")
    try:
        corpus = [_clean(state["resume_text"]), _clean(state["jd_text"])]
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", max_features=5000)
        matrix = vectorizer.fit_transform(corpus)
        score = round(float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0]) * 100, 1)
        kw = _keyword_analysis(state["resume_text"], state["jd_text"])
        log.info("baseline_agent_done", score=score)
        return {"baseline_score": score, "keyword_analysis": kw}
    except Exception as e:
        log.error("baseline_agent_failed", error=str(e))
        return {"baseline_score": 0.0, "keyword_analysis": {}}
