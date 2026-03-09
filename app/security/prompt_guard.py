from __future__ import annotations

import re

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# Basic patterns for prompt injection/jailbreak detection
_INJECTION_PATTERNS = [
    r"(?i)\bignore\b.*\bprevious\b.*\binstructions\b",
    r"(?i)\byou are now\b",
    r"(?i)\bacting as\b",
    r"(?i)\bsystem prompt\b",
    r"(?i)\[system\]",
    r"(?i)\[developer\]",
    r"(?i)\[user\]",
    r"(?i)\bDAN\b",
    r"(?i)\bjailbreak\b",
    r"(?i)\bdo anything now\b",
]


class PromptGuard:
    """Security layer to detect and prevent prompt injection and malicious inputs."""

    def __init__(self, patterns: list[str] | None = None):
        self.regexes = [re.compile(p) for p in (patterns or _INJECTION_PATTERNS)]

    def scan(self, text: str) -> bool:
        """
        Scan text for suspicious patterns.
        Returns True if text is flagged as suspicious.
        """
        t = (text or "").strip()
        if not t:
            return False

        for rex in self.regexes:
            if rex.search(t):
                _LOG.warning(f"Suspect input detected: pattern match '{rex.pattern}'")
                return True
        return False

    def sanitize(self, text: str) -> str:
        """Basic character-level sanitization."""
        # Remove non-printable characters or obvious script tags if needed
        return text.replace("<script>", "").replace("</script>", "").strip()
