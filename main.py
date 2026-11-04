"""Compatibility import; the canonical FastAPI app lives in app.main."""
from app.main import app, BLOCKED_TERMS, DETECTION_ONLY  # noqa: F401
