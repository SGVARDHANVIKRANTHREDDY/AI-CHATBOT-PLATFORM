# Security Model

> Complete documentation of the security architecture: authentication, authorization, prompt injection defense, content safety, plugin sandboxing, and abuse detection.

---

## Table of Contents

- [Overview](#overview)
- [Authentication](#authentication)
  - [JWT Authentication](#jwt-authentication)
  - [API Key Authentication](#api-key-authentication)
- [Request Security Middleware](#request-security-middleware)
- [Rate Limiting](#rate-limiting)
- [Prompt Guard](#prompt-guard)
- [Content Safety Filter](#content-safety-filter)
- [Refusal Guard](#refusal-guard)
- [Abuse Detection](#abuse-detection)
- [Plugin Sandboxing](#plugin-sandboxing)
- [Response Security](#response-security)
- [Data Protection](#data-protection)
- [Configuration](#configuration)
- [Threat Model](#threat-model)

---

## Overview

The security model provides defense-in-depth across the entire request lifecycle:

```
Request
  │
  ▼
┌──────────────────┐
│ Security Headers │ Content-Security-Policy, X-Frame-Options, etc.
└────────┬─────────┘
         │
┌────────▼─────────┐
│ Request Size     │ Max 1MB body
│ Limiter          │
└────────┬─────────┘
         │
┌────────▼─────────┐
│ Rate Limiter     │ Token bucket (Redis Lua)
└────────┬─────────┘
         │
┌────────▼─────────┐
│ JWT / API Key    │ Authentication
│ Auth             │
└────────┬─────────┘
         │
┌────────▼─────────┐
│ PromptGuard      │ Input sanitization
└────────┬─────────┘
         │
┌────────▼─────────┐
│ ContentSafety    │ Injection detection
│ Filter           │
└────────┬─────────┘
         │
┌────────▼─────────┐
│ RefusalGuard     │ Low-confidence refusal
└────────┬─────────┘
         │
┌────────▼─────────┐
│ Response         │ Output sanitization
│ Validator        │
└────────┘
```

---

## Authentication

### JWT Authentication

**Location:** `app/api/middleware/auth.py`

```python
class JWTAuthMiddleware:
    async def __call__(self, request: Request, call_next):
        # Skip auth for health and docs endpoints
        if request.url.path in ("/health", "/docs", "/openapi.json"):
            return await call_next(request)

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "Missing token"})

        token = auth_header[7:]

        try:
            # Decode and verify JWT
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
            request.state.user_id = payload.get("sub")
            request.state.session_id = payload.get("session_id")
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"error": "Token expired"})
        except jwt.InvalidTokenError:
            return JSONResponse(status_code=401, content={"error": "Invalid token"})

        return await call_next(request)
```

### API Key Authentication

```python
class APIKeyAuth:
    async def verify(self, request: Request) -> bool:
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return False
        return api_key == settings.API_KEY
```

---

## Request Security Middleware

### Security Headers

**Location:** `app/api/middleware/security.py`

```python
class SecurityHeadersMiddleware:
    async def __call__(self, request: Request, call_next):
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        return response
```

### Request Size Limiter

```python
class RequestSizeLimitMiddleware:
    MAX_SIZE = 1_048_576  # 1MB

    async def __call__(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.MAX_SIZE:
            return JSONResponse(
                status_code=413,
                content={"error": "Request body too large"}
            )
        return await call_next(request)
```

### Correlation ID

```python
class CorrelationMiddleware:
    async def __call__(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
```

### Request Timeout

```python
class TimeoutMiddleware:
    TIMEOUT = 60  # seconds

    async def __call__(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.TIMEOUT)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={"error": "Request timed out"}
            )
```

---

## Rate Limiting

**Location:** `app/api/dependencies/rate_limiter.py`

### Token Bucket Algorithm

Implemented as a Redis Lua script for atomic, distributed rate limiting:

```python
RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local bucket = redis.call('hmget', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

-- Refill tokens based on elapsed time
local elapsed = now - last_refill
local refill = elapsed * rate
tokens = math.min(capacity, tokens + refill)

-- Consume one token
if tokens >= 1 then
    tokens = tokens - 1
    redis.call('hmset', key, 'tokens', tokens, 'last_refill', now)
    redis.call('expire', key, 3600)
    return 1  -- allowed
else
    redis.call('hmset', key, 'tokens', tokens, 'last_refill', now)
    redis.call('expire', key, 3600)
    return 0  -- rejected
end
"""
```

### Rate Limit Configuration

| Tier | Requests/Minute | Burst Capacity |
|------|-----------------|----------------|
| Default | 30 | 30 |
| Authenticated | 60 | 60 |
| Premium | 120 | 120 |

---

## Prompt Guard

**Location:** `app/security/prompt_guard.py`

The `PromptGuard` sanitizes user input to remove injection attempts and dangerous content.

### Pattern Detection

```python
class PromptGuard:
    INJECTION_PATTERNS = [
        r'ignore\s+(all\s+)?previous\s+instructions',
        r'disregard\s+(all\s+)?prior\s+(instructions|prompts)',
        r'you\s+are\s+now\s+(a|an)\s+',
        r'system\s*:\s*',
        r'<\s*system\s*>',
        r'<\s*prompt\s*>',
        r'jailbreak',
        r'DAN\s+mode',
        r'do\s+anything\s+now',
        r'bypass\s+(safety|filter|restriction)',
    ]

    def check(self, text: str) -> Tuple[bool, float]:
        """Check text for injection patterns. Returns (is_safe, score)."""
        score = 0.0
        for pattern in self.INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score += 0.3
        return score < 0.5, score
```

### Input Sanitization

```python
def sanitize(self, text: str) -> str:
    """Remove dangerous content from user input."""
    # Remove script tags
    text = re.sub(r'<script\b[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Remove non-printable characters (control chars, zero-width)
    text = ''.join(c for c in text if c.isprintable() or c in '\n\r\t')

    # Remove null bytes
    text = text.replace('\x00', '')

    return text.strip()
```

---

## Content Safety Filter

**Location:** `app/security/content_filter.py`

The `ContentSafetyFilter` provides comprehensive content analysis with weighted scoring.

### Injection Rules (19 patterns)

```python
INJECTION_RULES = [
    {"pattern": r"ignore\s+previous\s+instructions", "weight": 0.9},
    {"pattern": r"you\s+are\s+now\s+", "weight": 0.8},
    {"pattern": r"system\s*prompt\s*:", "weight": 0.9},
    {"pattern": r"<\|.*?\|>", "weight": 0.7},      # Special tokens
    {"pattern": r"\\n\\n.*?\\n\\n", "weight": 0.4},  # Multi-line injection
    {"pattern": r"base64\s*:", "weight": 0.6},
    {"pattern": r"eval\s*\(", "weight": 0.7},
    {"pattern": r"exec\s*\(", "weight": 0.7},
    {"pattern": r"__import__", "weight": 0.8},
    {"pattern": r"subprocess", "weight": 0.7},
    {"pattern": r"os\.system", "weight": 0.8},
    {"pattern": r"rm\s+-rf", "weight": 0.9},
    # ... additional patterns
]
```

### Scoring Algorithm

```python
class ContentSafetyFilter:
    def analyze(self, content: str) -> dict:
        """Analyze content for safety threats."""
        total_score = 0.0
        matched_rules = []

        for rule in self.INJECTION_RULES:
            if re.search(rule["pattern"], content, re.IGNORECASE):
                total_score += rule["weight"]
                matched_rules.append(rule["pattern"])

        is_safe = total_score < 1.0  # Threshold

        return {
            "is_safe": is_safe,
            "threat_score": total_score,
            "matched_rules": matched_rules,
            "recommendation": "block" if not is_safe else "allow"
        }
```

### Quarantine Store

Unsafe content is quarantined for review:

```python
async def quarantine(self, content: str, analysis: dict, source: str):
    """Store unsafe content for security review."""
    entry = {
        "content": content[:500],  # Truncate for safety
        "analysis": analysis,
        "source": source,
        "timestamp": datetime.utcnow().isoformat()
    }
    await self.redis.rpush("quarantine:content", json.dumps(entry))
    await self.redis.ltrim("quarantine:content", -1000, -1)  # Keep last 1000
```

---

## Refusal Guard

**Location:** `app/security/refusal_guard.py`

The `RefusalGuard` refuses to answer when the system lacks sufficient context:

```python
class RefusalGuard:
    CONFIDENCE_THRESHOLD = 0.35

    def should_refuse(self, rag_score: float) -> bool:
        """Refuse to answer if RAG confidence is below threshold."""
        return rag_score < self.CONFIDENCE_THRESHOLD

    def get_refusal_message(self) -> str:
        return ("I don't have enough information to answer this question accurately. "
                "Could you provide more context or rephrase your question?")
```

**When refusal triggers:**
- RAG retrieval score < 0.35 (best match's relevance score)
- No relevant documents found in the index
- Query is outside the system's knowledge domain

---

## Abuse Detection

**Location:** `app/api/middleware/abuse.py`

```python
class AbuseDetectionMiddleware:
    async def __call__(self, request: Request, call_next):
        client_ip = request.client.host
        user_id = getattr(request.state, "user_id", None)

        # Check for abuse patterns
        if await self._is_abusive(client_ip, user_id):
            return JSONResponse(
                status_code=429,
                content={"error": "Abuse detected, request blocked"}
            )

        return await call_next(request)

    async def _is_abusive(self, ip: str, user_id: str) -> bool:
        """Detect abuse patterns."""
        # 1. Rapid-fire requests (>10 in 1 second)
        key = f"abuse:rapid:{ip}"
        count = await self.redis.incr(key)
        await self.redis.expire(key, 1)
        if count > 10:
            return True

        # 2. Known bad actors (blacklist)
        if await self.redis.sismember("abuse:blacklist", ip):
            return True

        return False
```

---

## Plugin Sandboxing

See [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md) for the full plugin security model. Summary:

| Layer | Protection |
|-------|-----------|
| **Subprocess isolation** | OS process boundary |
| **Environment sanitization** | Only PATH, TEMP, PYTHONPATH exposed |
| **Module restrictions** | 24 allowed, 28 blocked modules |
| **Builtin restrictions** | No exec/eval/compile/open/__import__ |
| **Static analysis** | AST-based dangerous call detection |
| **Execution timeout** | 30s per invocation |
| **IPC size limit** | 10MB per message |
| **Output capture** | 4KB per stream |

---

## Response Security

### Output Sanitization

The `ResponseValidator` (see [RELIABILITY_LAYER.md](RELIABILITY_LAYER.md)) sanitizes all outputs:

1. **Length truncation** — Prevents unbounded response sizes (4096 char limit)
2. **Tool call removal** — Strips `<tool_call: ...>` patterns from user-facing output
3. **Injection detection** — Scans for `<script>`, `javascript:`, `on*=` event handlers
4. **JSON schema validation** — Validates structured outputs match expected schemas

### Content Safety in RAG

The RAG pipeline filters content before indexing:

```python
# During document ingestion
safe_chunks = [c for c in chunks if self.safety_filter.is_safe(c)]
```

This prevents adversarial content from entering the vector index and being retrieved as context.

---

## Data Protection

### Sensitive Data Handling

| Data | Storage | Protection |
|------|---------|-----------|
| JWT Secret | Environment variable | Not logged, not in source |
| API Keys | Environment variable | Stripped from plugin env |
| User messages | Redis + PostgreSQL | Session-scoped, TTL'd |
| Conversation history | PostgreSQL | Access by session_id only |
| Vector embeddings | FAISS / Qdrant | No raw content in vectors |

### Logging Safety

```python
# From logging_config.py
# Sensitive fields are redacted in logs
SENSITIVE_FIELDS = ["password", "token", "api_key", "secret", "authorization"]
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `JWT_SECRET` | Required env var | JWT signing key |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `API_KEY` | Required env var | API key for key-based auth |
| Rate limit default | 30 req/min | Default rate limit |
| Request size max | 1MB | Maximum request body |
| Request timeout | 60s | Maximum request duration |
| RAG refusal threshold | 0.35 | Minimum RAG score to answer |
| Content safety threshold | 1.0 | Maximum injection score |
| Abuse rapid-fire limit | 10/s | Max requests per second per IP |
| Plugin timeout | 30s | Plugin execution limit |
| CORS origins | Configured | Allowed origin domains |

---

## Threat Model

| Threat | Vector | Mitigation |
|--------|--------|-----------|
| **Prompt injection** | User input | PromptGuard (10 patterns), ContentSafetyFilter (19 weighted rules) |
| **Jailbreak** | User input | Pattern detection, DAN mode detection |
| **Data exfiltration** | Tool calls | Plugin environment sanitization, sandbox restrictions |
| **DoS** | High request volume | Token bucket rate limiting, request queue limiter |
| **Path traversal** | File reader tool | Canonical path resolution, directory boundary check |
| **XSS** | LLM output | Response injection detection, HTML sanitization |
| **Token theft** | Network interception | HTTPS enforcement, HSTS headers |
| **Clickjacking** | Embedded frames | X-Frame-Options: DENY |
| **MIME sniffing** | File uploads | X-Content-Type-Options: nosniff |
| **RAG poisoning** | Malicious documents | Content safety filter on ingestion |
| **Hallucination** | LLM output | CriticAgent evaluation, RefusalGuard |
| **Code injection** | Calculator tool | AST whitelist, no arbitrary code execution |
| **Abuse** | Automated attacks | Rapid-fire detection, IP blacklisting |
| **Session hijacking** | Stolen JWT | Short TTL, signature verification, no token embedding in URLs |
| **Replay attacks** | Captured requests | Nonce in correlation ID, request timestamp validation |
| **Model extraction** | Repeated queries | Rate limiting, response truncation, no raw logits exposed |
| **Supply chain** | Compromised deps | `pip-audit`, pinned versions, Bandit static scanning |

---

## OWASP Top 10 Mapping

| OWASP Category | Coverage | Implementation |
|----------------|----------|---------------|
| **A01 — Broken Access Control** | ✅ | JWT + API key auth, per-user rate limits, session-scoped memory |
| **A02 — Cryptographic Failures** | ✅ | HMAC-SHA256 JWT signing, HTTPS enforcement, no secrets in logs |
| **A03 — Injection** | ✅ | PromptGuard (19 rules), HTML sanitization, AST-restricted eval |
| **A04 — Insecure Design** | ✅ | Defense in depth (7 middleware layers), plugin sandboxing |
| **A05 — Security Misconfiguration** | ✅ | Strict security headers, minimal CORS, env-only secrets |
| **A06 — Vulnerable Components** | ✅ | Pinned dependencies, Bandit scanning, `pip-audit` in CI |
| **A07 — Auth Failures** | ✅ | JWT expiry, API key validation, no hardcoded credentials |
| **A08 — Data Integrity Failures** | ✅ | Content safety filter on ingestion, trust scoring, RAG hash verification |
| **A09 — Logging & Monitoring** | ✅ | Structured ELK logging, abuse detection, SIEM-compatible events |
| **A10 — SSRF** | ✅ | Knowledge crawler whitelist, domain allowlist for web search |

---

## Security Testing

### Automated Security Checks

```bash
# Static analysis for security issues
bandit -r app/ -c pyproject.toml

# Dependency vulnerability scanning
pip-audit

# Lint for security-relevant patterns
ruff check app/ --select S   # S = bandit-integrated rules
```

### Testing Prompt Injection

```bash
# The test suite includes dedicated injection tests
pytest tests/test_content_safety.py -v

# Test scenarios include:
# - "Ignore previous instructions" variants
# - DAN (Do Anything Now) jailbreak
# - System prompt extraction attempts
# - Role-switching attacks
# - Unicode obfuscation
```

### Testing API Security

```bash
# Request protection middleware tests
pytest tests/test_api_protection.py -v

# Test scenarios include:
# - Oversized request bodies
# - Rate limit enforcement
# - Missing authentication
# - Path traversal in inputs
# - Header injection
```

### Plugin Sandbox Verification

```bash
# Plugin isolation tests
pytest tests/test_plugin_isolation.py -v

# Verifies:
# - No access to environment variables
# - No filesystem access outside sandbox
# - No network access
# - Blocked imports (os, sys, subprocess, socket)
# - Execution timeout enforcement
```

---

## Security Architecture Summary

```
Request Lifecycle — Security Checkpoints
═══════════════════════════════════════════

  Client Request
       │
       ▼
  ┌──────────────────────┐
  │  TLS Termination     │  ← HTTPS only (HSTS enforced)
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  Security Headers    │  ← X-Frame-Options, CSP, nosniff
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  Request Size Check  │  ← 1MB limit (rejects oversized)
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  Abuse Detection     │  ← Rapid-fire, missing UA, path traversal
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  Rate Limiting       │  ← Token bucket per user (100/min)
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  JWT / API Key Auth  │  ← Identity verification
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  Prompt Guard        │  ← Injection pattern scanning
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  Content Safety      │  ← Weighted rule matching (19 rules)
  └──────────┬───────────┘
             ▼
       Processing ...
             │
             ▼
  ┌──────────────────────┐
  │  Response Validator  │  ← Length, injection, hallucination check
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │  Output Sanitization │  ← Tool call removal, HTML escaping
  └──────────┬───────────┘
             ▼
     Client Response
```

---

## Incident Response

If a security issue is discovered:

1. **Immediate** — Enable stricter rate limits via `API_RATE_LIMIT=10` in environment
2. **Investigate** — Check structured logs for SIEM abuse signals (`event=abuse_signal`)
3. **Contain** — Block offending IPs at the load balancer or reverse proxy
4. **Remediate** — Update injection patterns in `content_safety.py`, redeploy
5. **Report** — Log the incident with timeline, root cause, and fix in the security log
