"""
Response Guard — LLM response validation layer.

Validates LLM outputs before they reach the user or trigger tool execution.
Catches hallucinated tool calls, malformed JSON, prompt injection in outputs,
and excessively long responses.

Design rationale:
    LLMs are non-deterministic.  Even well-prompted models can:
    • Reference tools that don't exist ("hallucinated tool calls")
    • Emit malformed JSON when a structured format is expected
    • Echo back prompt injection patterns from user input
    • Generate runaway responses that exceed context budgets
    Structural validation at the boundary catches these *before* downstream
    code acts on the response.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# ─── Prompt injection patterns (in LLM *output*) ─────────────────
_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)"),
    re.compile(r"(?i)you\s+are\s+now\s+(a|an|the)\s+"),
    re.compile(r"(?i)system\s*:\s*"),
    re.compile(r"(?i)\\n\s*system\s*:"),
    re.compile(r"(?i)act\s+as\s+(if|though)\s+you"),
    re.compile(r"(?i)(jailbreak|DAN|do anything now)", re.IGNORECASE),
    re.compile(r"<\s*/?script", re.IGNORECASE),
]

# Tool call format used by the platform's ToolRunner
_TOOL_CALL_PATTERN = re.compile(r"<tool_call:\s*(\w+)\(.*?\)>")


@dataclass
class ValidationIssue:
    """Single validation problem found in a response."""
    category: str  # "hallucinated_tool", "invalid_json", "injection", "length"
    severity: str  # "warning", "error"
    message: str
    auto_fixed: bool = False


@dataclass
class ValidationResult:
    """Outcome of response validation."""
    is_valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)
    sanitized_response: Optional[str] = None
    original_response: str = ""

    def add_issue(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "error":
            self.is_valid = False


class ResponseValidator:
    """Validates and sanitizes LLM responses.

    Args:
        known_tools: Set of valid tool names the system supports.
        max_response_length: Maximum allowed response length in characters.
        strict_mode: If True, any issue makes the response invalid.
                     If False, only ``severity="error"`` issues do.
    """

    def __init__(
        self,
        known_tools: Optional[Set[str]] = None,
        max_response_length: int = 16_000,
        strict_mode: bool = False,
    ) -> None:
        self.known_tools = known_tools or set()
        self.max_response_length = max_response_length
        self.strict_mode = strict_mode

    def validate(
        self,
        response: str,
        *,
        expected_json_schema: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ValidationResult:
        """Run all validation checks on a response.

        Args:
            response: Raw LLM output text.
            expected_json_schema: Optional JSON schema to validate against.
            context: Additional context (e.g. question, session info).

        Returns:
            ValidationResult with issues and sanitized response.
        """
        result = ValidationResult(original_response=response)
        sanitized = response

        # 1. Length limits
        sanitized = self._check_length(sanitized, result)

        # 2. Hallucinated tool calls
        sanitized = self._check_hallucinated_tools(sanitized, result)

        # 3. JSON schema validation (if expected)
        if expected_json_schema is not None:
            self._check_json_schema(sanitized, expected_json_schema, result)

        # 4. Prompt injection detection
        self._check_prompt_injection(sanitized, result)

        # Apply strict mode
        if self.strict_mode and result.issues:
            result.is_valid = False

        result.sanitized_response = sanitized

        if result.issues:
            _LOG.warning(
                "ResponseValidator: %d issue(s) found — valid=%s",
                len(result.issues),
                result.is_valid,
            )
            for issue in result.issues:
                _LOG.info(
                    "  [%s] %s: %s (auto_fixed=%s)",
                    issue.severity,
                    issue.category,
                    issue.message,
                    issue.auto_fixed,
                )

        return result

    # ── Individual checks ─────────────────────────────────────────

    def _check_length(self, response: str, result: ValidationResult) -> str:
        """Enforce response length limits with truncation."""
        if len(response) > self.max_response_length:
            truncated = response[: self.max_response_length]
            # Try to truncate at a sentence boundary
            last_period = truncated.rfind(".")
            if last_period > self.max_response_length * 0.8:
                truncated = truncated[: last_period + 1]

            result.add_issue(
                ValidationIssue(
                    category="length",
                    severity="warning",
                    message=f"Response truncated from {len(response)} to {len(truncated)} chars",
                    auto_fixed=True,
                )
            )
            return truncated + "\n\n[Response truncated due to length limits]"
        return response

    def _check_hallucinated_tools(
        self, response: str, result: ValidationResult
    ) -> str:
        """Detect tool calls to non-existent tools."""
        matches = _TOOL_CALL_PATTERN.findall(response)
        sanitized = response

        for tool_name in matches:
            if tool_name not in self.known_tools:
                result.add_issue(
                    ValidationIssue(
                        category="hallucinated_tool",
                        severity="error",
                        message=f"Hallucinated tool call: '{tool_name}' is not a registered tool",
                        auto_fixed=True,
                    )
                )
                # Remove the hallucinated tool call from the response
                sanitized = re.sub(
                    rf"<tool_call:\s*{re.escape(tool_name)}\(.*?\)>",
                    f"[Removed: unknown tool '{tool_name}']",
                    sanitized,
                )
        return sanitized

    def _check_json_schema(
        self,
        response: str,
        schema: Dict[str, Any],
        result: ValidationResult,
    ) -> None:
        """Validate JSON output against an expected schema."""
        # Extract JSON from response
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match is None:
            result.add_issue(
                ValidationIssue(
                    category="invalid_json",
                    severity="error",
                    message="Expected JSON output but none found in response",
                )
            )
            return

        try:
            parsed = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            result.add_issue(
                ValidationIssue(
                    category="invalid_json",
                    severity="error",
                    message=f"Malformed JSON in response: {e}",
                )
            )
            return

        # Validate required fields from schema
        required_fields = schema.get("required", [])
        properties = schema.get("properties", {})

        for field_name in required_fields:
            if field_name not in parsed:
                result.add_issue(
                    ValidationIssue(
                        category="invalid_json",
                        severity="error",
                        message=f"Missing required field: '{field_name}'",
                    )
                )

        # Type checking for present fields
        for field_name, field_schema in properties.items():
            if field_name in parsed:
                expected_type = field_schema.get("type")
                value = parsed[field_name]
                type_map = {
                    "string": str,
                    "integer": int,
                    "number": (int, float),
                    "boolean": bool,
                    "array": list,
                    "object": dict,
                }
                if expected_type and expected_type in type_map:
                    py_type = type_map[expected_type]
                    if not isinstance(value, py_type):
                        result.add_issue(
                            ValidationIssue(
                                category="invalid_json",
                                severity="warning",
                                message=f"Field '{field_name}' expected type '{expected_type}', got '{type(value).__name__}'",
                            )
                        )

    def _check_prompt_injection(
        self, response: str, result: ValidationResult
    ) -> None:
        """Detect prompt injection patterns in LLM output."""
        for pattern in _INJECTION_PATTERNS:
            match = pattern.search(response)
            if match:
                result.add_issue(
                    ValidationIssue(
                        category="injection",
                        severity="warning",
                        message=f"Potential prompt injection pattern detected: '{match.group(0)[:80]}'",
                    )
                )
