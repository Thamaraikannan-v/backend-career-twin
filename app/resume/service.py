import fitz  # PyMuPDF
from app.db.client import get_db
from app.core.exceptions import PDFParseError
import structlog

log = structlog.get_logger()


def parse_pdf(file_bytes: bytes) -> dict:
    """
    Extract clean text from a PDF resume.
    Returns dict with text, char_count, page_count.
    Raises PDFParseError if extraction fails or content is too short.
    """
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = []

        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text)

        page_count = len(doc)
        doc.close()

        full_text = "\n\n".join(pages).strip()

        if len(full_text) < 100:
            raise PDFParseError(
                "Extracted text is too short — the PDF may be scanned or image-only."
            )

        log.info("pdf_parsed", chars=len(full_text), pages=page_count)
        return {
            "text":       full_text,
            "char_count": len(full_text),
            "page_count": page_count,
        }

    except PDFParseError:
        raise
    except Exception as e:
        log.error("pdf_parse_failed", error=str(e))
        raise PDFParseError(f"Could not read PDF: {e}")


async def upload_to_storage(user_id: str, analysis_id: str, file_bytes: bytes) -> str:
    """
    Upload the raw PDF to Supabase Storage.
    Path: resumes/{user_id}/{analysis_id}.pdf
    Returns the public (signed) path string.
    """
    path = f"{user_id}/{analysis_id}.pdf"
    try:
        get_db().storage.from_("resumes").upload(
            path=path,
            file=file_bytes,
            file_options={"content-type": "application/pdf"},
        )
        log.info("resume_uploaded", path=path)
        return path
    except Exception as e:
        # Storage upload failure is non-fatal — analysis can still run
        log.error("resume_upload_failed", error=str(e))
        return ""
