"""Shared safe failure classification for v13 Reviewer entry points."""
from __future__ import annotations

PROTOCOL_HTTP_STATUSES=frozenset({400,404,405,415,422})

def classify_reviewer_http(error_code:str|None,status_code:int|None)->str:
    """Classify metadata only; callers must never pass or expose response bodies."""
    code=error_code or "PROVIDER"
    if code.startswith("REVIEWER_PROTOCOL") or status_code in PROTOCOL_HTTP_STATUSES:return "protocol"
    return "provider"
