from fastapi import HTTPException


class FreeTierLimitError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=402,
            detail="Free tier limit reached (3 analyses). Upgrade to Pro.",
        )


class AnalysisNotFoundError(HTTPException):
    def __init__(self):
        super().__init__(status_code=404, detail="Analysis not found.")


class PDFParseError(HTTPException):
    def __init__(self, detail: str = "Could not read PDF file."):
        super().__init__(status_code=422, detail=detail)


class UnauthorizedError(HTTPException):
    def __init__(self, detail: str = "Invalid or expired token."):
        super().__init__(status_code=401, detail=detail)
