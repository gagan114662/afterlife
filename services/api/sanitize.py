"""Input sanitization helpers for the conversation API."""
import re

_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9\s\-']")


def sanitize_name(name: str) -> str:
    """Strip characters unsafe for use in prompts or DB queries."""
    return _SAFE_NAME_PATTERN.sub("", name).strip()[:100]
