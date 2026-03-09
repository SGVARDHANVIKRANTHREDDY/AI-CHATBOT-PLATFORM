# Reliability Layer

> Complete documentation of circuit breakers, retry policies, timeout controllers, failure tracking, response validation, and load guards.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Circuit Breaker](#circuit-breaker)
- [Retry Policy](#retry-policy)
- [Timeout Controller](#timeout-controller)
- [Failure Tracker](#failure-tracker)
- [Response Validator](#response-validator)
- [Load Guards](#load-guards)
  - [Request Queue Limiter](#request-queue-limiter)
  - [Agent Execution Limiter](#agent-execution-limiter)
  - [Swarm Throttle](#swarm-throttle)
- [Integration Points](#integration-points)
- [Configuration](#configuration)
- [Failure Modes](#failure-modes)

---

## Overview

The reliability layer provides fault tolerance across all system components through a defense-in-depth approach:

```
Request → LoadGuard → Timeout → CircuitBreaker → Retry → Service Call
                                                          ↓
                                                   FailureTracker
                                                          ↓
                                                   ResponseValidator
```

Each layer addresses a different failure mode:

| Component | Failure Type | Strategy |
|-----------|-------------|----------|
| CircuitBreaker | Persistent service failures | Stop calling failed service |
| RetryPolicy | Transient failures | Retry with backoff |
| TimeoutController | Hung operations | Enforce wall-clock limits |
| FailureTracker | Failure rate monitoring | Sliding window statistics |
| ResponseValidator | Bad LLM output | Sanitize and validate responses |
| LoadGuard | Overload | Queue limiting, throttling |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Reliability Layer                      │
│                                                         │
│  ┌──────────────────┐  ┌──────────────────────────────┐ │
│  │ LoadGuard        │  │ ResponseValidator            │ │
│  │ ┌──────────────┐ │  │ • Length truncation           │ │
│  │ │ RequestQueue │ │  │ • Hallucinated tool removal   │ │
│  │ │ Limiter      │ │  │ • JSON schema check          │ │
│  │ └──────────────┘ │  │ • Injection detection         │ │
│  │ ┌──────────────┐ │  └──────────────────────────────┘ │
│  │ │ AgentExec    │ │                                   │
│  │ │ Limiter      │ │  ┌──────────────────────────────┐ │
│  │ └──────────────┘ │  │ FailureTracker               │ │
│  │ ┌──────────────┐ │  │ • Thread-safe deque           │ │
│  │ │ SwarmThrottle│ │  │ • Sliding window stats        │ │
│  │ └──────────────┘ │  │ • Failure rate calculation    │ │
│  └──────────────────┘  └──────────────────────────────┘ │
│                                                         │
│  ┌──────────────────┐  ┌──────────────────────────────┐ │
│  │ CircuitBreaker   │  │ RetryPolicy                  │ │
│  │ CLOSED→OPEN→     │  │ • Decorrelated jitter        │ │
│  │ HALF_OPEN        │  │ • Configurable exceptions    │ │
│  └──────────────────┘  └──────────────────────────────┘ │
│                                                         │
│  ┌──────────────────┐                                   │
│  │ TimeoutController│                                   │
│  │ asyncio.wait_for │                                   │
│  └──────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
```

---

## Circuit Breaker

**Location:** `app/reliability/circuit_breaker.py`

The circuit breaker prevents cascading failures by temporarily stopping calls to services experiencing persistent failures.

### State Machine

```
     success / below threshold
  ┌─────────────────────────────┐
  │                             │
  ▼         failure count       │
┌────────┐  ≥ threshold   ┌────┴───┐
│ CLOSED │ ──────────────▶│  OPEN  │
│(normal)│                │(reject)│
└────────┘                └────┬───┘
  ▲                            │
  │   success in              │ recovery_timeout
  │   half-open    ┌──────────▼──┐
  └────────────────│  HALF_OPEN  │
                   │ (test one)  │
                   └─────────────┘
```

### Implementation

```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.failure_tracker = FailureTracker()

    async def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_recovery():
                self.state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerOpen(f"Circuit breaker is OPEN")

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def _on_success(self):
        """Reset on successful call."""
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
        # Record success metric
        CIRCUIT_BREAKER_STATE.labels(name=self.name).set(0)

    def _on_failure(self, error):
        """Track failure and potentially open circuit."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        self.failure_tracker.record_failure(str(error))

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            CIRCUIT_BREAKER_STATE.labels(name=self.name).set(1)
            logger.warning(f"Circuit breaker OPENED after {self.failure_count} failures")

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to try recovery."""
        if self.last_failure_time is None:
            return True
        return (time.time() - self.last_failure_time) >= self.recovery_timeout
```

### Circuit Breaker in LLM Providers

The `FallbackProvider` uses circuit breakers per LLM provider:

```python
class FallbackProvider:
    def __init__(self, providers: List[LLMProvider]):
        self.providers = providers
        self.circuit_breakers = {
            p.name: CircuitBreaker(failure_threshold=3, recovery_timeout=60)
            for p in providers
        }

    async def ask(self, prompt, **kwargs):
        for provider in self.providers:
            cb = self.circuit_breakers[provider.name]
            try:
                return await cb.call(provider.ask, prompt, **kwargs)
            except CircuitBreakerOpen:
                continue  # Try next provider
            except Exception:
                continue  # Circuit breaker records failure
        raise AllProvidersUnavailable("All LLM providers failed")
```

---

## Retry Policy

**Location:** `app/reliability/retry.py`

Implements the **decorrelated jitter** pattern from the AWS Architecture Blog for optimal retry distribution.

### Algorithm

```python
class RetryPolicy:
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0,
                 max_delay: float = 30.0, retryable_exceptions: tuple = (Exception,)):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.retryable_exceptions = retryable_exceptions

    async def execute(self, func, *args, **kwargs):
        """Execute with decorrelated jitter backoff."""
        last_delay = self.base_delay

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except self.retryable_exceptions as e:
                if attempt == self.max_retries:
                    raise

                # Decorrelated jitter: delay = random(base, last_delay * 3)
                last_delay = min(
                    self.max_delay,
                    random.uniform(self.base_delay, last_delay * 3)
                )

                logger.warning(f"Retry {attempt + 1}/{self.max_retries}, "
                             f"delay={last_delay:.2f}s, error={e}")
                await asyncio.sleep(last_delay)
```

### Decorrelated Jitter

Unlike exponential backoff (which causes retry storms), decorrelated jitter spreads retries more evenly:

```
Exponential:   1s → 2s → 4s → 8s → 16s  (deterministic)
Full jitter:   [0,1] → [0,2] → [0,4]    (too aggressive early)
Decorrelated:  [1,3] → [1,9] → [1,27]   (well-spread, capped at max)
```

---

## Timeout Controller

**Location:** `app/reliability/timeout.py`

```python
class TimeoutController:
    def __init__(self, default_timeout: float = 30.0):
        self.default_timeout = default_timeout

    async def execute(self, func, *args, timeout: float = None, **kwargs):
        """Execute with asyncio timeout."""
        t = timeout or self.default_timeout
        try:
            return await asyncio.wait_for(func(*args, **kwargs), timeout=t)
        except asyncio.TimeoutError:
            raise OperationTimeout(f"Operation timed out after {t}s")

    async def execute_with_fallback(self, func, fallback_fn, *args,
                                      timeout: float = None, **kwargs):
        """Execute with timeout, falling back to fallback_fn on timeout."""
        try:
            return await self.execute(func, *args, timeout=timeout, **kwargs)
        except OperationTimeout:
            logger.warning("Operation timed out, executing fallback")
            return await fallback_fn(*args, **kwargs)
```

---

## Failure Tracker

**Location:** `app/reliability/failure_tracker.py`

Thread-safe failure tracking with sliding window statistics:

```python
class FailureTracker:
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.failures = deque(maxlen=window_size)  # Thread-safe
        self.lock = threading.Lock()

    def record_failure(self, error: str):
        """Record a failure with timestamp."""
        with self.lock:
            self.failures.append({
                "error": error,
                "timestamp": datetime.utcnow().isoformat()
            })

    def get_failure_rate(self, window_seconds: int = 60) -> float:
        """Calculate failure rate in the last N seconds."""
        cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
        with self.lock:
            recent = [
                f for f in self.failures
                if datetime.fromisoformat(f["timestamp"]) > cutoff
            ]
        return len(recent) / max(self.window_size, 1)

    def get_stats(self) -> dict:
        """Get failure statistics."""
        with self.lock:
            return {
                "total_failures": len(self.failures),
                "failure_rate_1m": self.get_failure_rate(60),
                "failure_rate_5m": self.get_failure_rate(300),
                "recent_errors": list(self.failures)[-5:],
            }
```

---

## Response Validator

**Location:** `app/reliability/response_validator.py`

Validates and sanitizes LLM responses before sending to users:

### Validation Steps

```python
class ResponseValidator:
    def validate(self, response: str) -> str:
        """Validate and sanitize an LLM response."""

        # 1. Length truncation
        response = self._truncate(response, max_length=4096)

        # 2. Remove hallucinated tool calls
        response = self._remove_hallucinated_tools(response)

        # 3. JSON schema validation (if structured output expected)
        if self.expect_json:
            response = self._validate_json_schema(response)

        # 4. Injection detection
        if self._detect_injection(response):
            response = self._sanitize_injection(response)

        return response
```

### Hallucinated Tool Removal

```python
def _remove_hallucinated_tools(self, response: str) -> str:
    """Remove tool calls that appeared in LLM output but shouldn't be visible to users."""
    # Remove <tool_call: ...> patterns from final response
    return re.sub(r'<tool_call:\s*\w+\([^)]*\)>', '', response)
```

### Injection Detection

```python
def _detect_injection(self, response: str) -> bool:
    """Detect potential injection patterns in LLM output."""
    patterns = [
        r'<script\b',
        r'javascript:',
        r'on\w+\s*=',
        r'data:text/html',
        r'<!--.*?-->',
    ]
    return any(re.search(p, response, re.IGNORECASE) for p in patterns)
```

---

## Load Guards

### Request Queue Limiter

**Location:** `app/reliability/load_guard.py`

```python
class RequestQueueLimiter:
    """Limit the total number of concurrent requests."""

    def __init__(self, max_concurrent: int = 100):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.current = 0

    async def acquire(self):
        acquired = self.semaphore.acquire()
        if not acquired:
            raise TooManyRequests("Request queue full")
        self.current += 1

    async def release(self):
        self.semaphore.release()
        self.current -= 1
```

### Agent Execution Limiter

```python
class AgentExecutionLimiter:
    """System-wide semaphore for concurrent agent execution."""

    def __init__(self, max_concurrent: int = 20):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def acquire(self):
        await self.semaphore.acquire()

    def release(self):
        self.semaphore.release()
```

### Swarm Throttle

See [SWARM_EXECUTION.md](SWARM_EXECUTION.md) for the `SwarmThrottle` pressure-based parallelism system.

---

## Integration Points

The reliability primitives are composed in this order for every external call:

```
   Incoming Request
        │
        ▼
┌─────────────────┐
│ RequestQueueLimiter │  ← Reject if system is overloaded (429)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ TimeoutController│  ← Cancel if operation exceeds 30s
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ CircuitBreaker   │  ← Reject if service is known-down (fast fail)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ RetryPolicy      │  ← Retry on transient failure (decorrelated jitter)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Service Call     │  ← Actual LLM / Redis / DB call
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ FailureTracker   │  ← Record success/failure for sliding window stats
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ResponseValidator│  ← Sanitize LLM output before returning to user
└─────────────────┘
```

### Example: LLM Call Protection

```python
# How the FallbackProvider uses reliability primitives:
async def ask(self, prompt, **kwargs):
    for provider in self.providers:
        cb = self.circuit_breakers[provider.name]
        try:
            # Circuit breaker wraps the actual call
            result = await cb.call(provider.ask, prompt, **kwargs)
            return result
        except CircuitBreakerOpen:
            # This provider is known-down, skip instantly (< 1ms)
            continue
        except Exception:
            # Circuit breaker records the failure internally
            continue
    raise AllProvidersUnavailable("All LLM providers failed")
```

---

## Configuration

### Circuit Breaker Timing

| Parameter | Default | Description |
|-----------|---------|-------------|
| `failure_threshold` | **3** | Consecutive failures before opening the circuit |
| `recovery_timeout` | **60s** | Time in OPEN state before attempting recovery (HALF_OPEN) |

**State machine timing:**

```
CLOSED ──(3 failures in succession)──▶ OPEN
  ▲                                       │
  │                                       │ Wait 60 seconds
  │                                       ▼
  └──────(1 success)──────────────── HALF_OPEN
                                       │
                                       │ (1 failure)
                                       ▼
                                     OPEN (restart 60s timer)
```

- **CLOSED → OPEN**: After 3 consecutive failures (no time window — purely count-based)
- **OPEN duration**: Exactly `recovery_timeout` seconds (default 60s). All calls during this period are rejected instantly with `CircuitBreakerOpen`
- **HALF_OPEN**: Allows exactly **one** test request through. If it succeeds → CLOSED. If it fails → back to OPEN for another 60s
- **Reset on success**: A single successful call in CLOSED state resets `failure_count` to 0

### Retry Policy Timing

| Parameter | Default | Range |
|-----------|---------|-------|
| `max_retries` | **3** | Total attempts (1 original + 3 retries = 4 total) |
| `base_delay` | **1.0s** | Minimum delay between retries |
| `max_delay` | **30.0s** | Maximum delay cap |

**Decorrelated jitter formula:**

```
delay = min(max_delay, random(base_delay, last_delay × 3))
```

Example retry sequence (typical):
```
Attempt 1: immediate
Attempt 2: wait ~1.5s  (random between 1.0 and 3.0)
Attempt 3: wait ~4.2s  (random between 1.0 and 4.5)
Attempt 4: wait ~9.8s  (random between 1.0 and 12.6)
Total worst-case: ~15.5s before final failure
```

### Timeout Defaults

| Context | Default Timeout |
|---------|----------------|
| LLM API call | 30s (`TimeoutController`) |
| HTTP request | 60s (`TimeoutMiddleware`) |
| Agent execution | 30s (`AgentWatchdog`) |
| Plugin execution | 10s (`asyncio.wait_for`) |

---

## Configuration Trade-offs

| If You Change | Effect | Risk |
|---------------|--------|------|
| `failure_threshold` 3 → 1 | Circuit opens after first failure | Flapping — circuit opens from a single transient error, causing unnecessary failovers |
| `failure_threshold` 3 → 10 | More tolerance before opening | Slow degradation — 10 failed requests pile up before the circuit protects the system |
| `recovery_timeout` 60s → 10s | Faster recovery probes | Hammering a struggling service — if it's still recovering, rapid probes make it worse |
| `recovery_timeout` 60s → 300s | Long wait before retrying | Extended outage — even if the service recovers at 90s, you wait until 300s |
| `max_retries` 3 → 0 | No retries | Transient failures (network blips, 502s) aren't tolerated — fail on first error |
| `max_retries` 3 → 10 | Aggressive retrying | Pile-up — 10 retries × multiple requests = retry storm overwhelming the backend |
| `base_delay` 1.0s → 0.1s | Fast retries | Server gets hammered before it recovers |
| Timeout 30s → 5s | Fast failure | HuggingFace cold starts (10-30s) always timeout — LLM calls fail consistently |
| Timeout 30s → 120s | Very patient | Hung requests tie up workers for 2 minutes, degrading the whole system |

---

## Metrics to Monitor for Tuning

| Metric | Healthy Range | What It Means If Outside Range |
|--------|--------------|-------------------------------|
| `circuit_breaker_state` | 0 (CLOSED) | 1 = OPEN: a provider is down. Check provider health. 2 = HALF_OPEN: recovering |
| `circuit_breaker_transitions_total` | < 2/hour | Frequent transitions = flapping. Increase `failure_threshold` or check underlying service stability |
| `retry_attempts_total` | < 10% of requests | High retry rate = unreliable backend. Investigate root cause, don't just increase retries |
| `timeout_exceeded_total` | < 5% of requests | High timeout rate = backend is slow. Check LLM latency or increase timeout |
| `failure_rate_1m` | < 0.05 (5%) | > 5% = system degradation. Check circuit breaker state and provider health |
| `request_queue_size` | < 50 | > 80% of capacity = approaching overload. Scale horizontally or increase limits |

---

## Failure Modes

| Component | Failure | Impact | Recovery |
|-----------|---------|--------|----------|
| CircuitBreaker | OPEN state | Calls rejected instantly | Automatic after `recovery_timeout` (60s) |
| RetryPolicy | Max retries exhausted | Operation fails | Error surfaced to caller |
| TimeoutController | Timeout exceeded | Operation cancelled | `OperationTimeout` raised; fallback if configured |
| FailureTracker | Thread contention | Metric delay | Lock contention is brief (microseconds) |
| ResponseValidator | False positive injection | Over-sanitized response | Tune detection regex patterns |
| RequestQueueLimiter | Semaphore exhausted | 429 Too Many Requests | Client retries after `Retry-After` header |
| AgentExecutionLimiter | Semaphore exhausted | Agent queued | AgentWatchdog may timeout the wait |
| SwarmThrottle | High pressure detected | Parallelism reduced to 2 | Auto-recovers as load drops |
    """Limit concurrent agent executions system-wide."""

    def __init__(self, max_concurrent: int = 20):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def execute(self, agent_fn, *args, **kwargs):
        async with self.semaphore:
            return await agent_fn(*args, **kwargs)
```

### Swarm Throttle

```python
class SwarmThrottle:
    """Dynamically adjust swarm parallelism based on system pressure."""

    def __init__(self, max_parallel: int = 10):
        self.max_parallel = max_parallel
        self.current_load = 0

    def get_allowed_parallelism(self) -> int:
        pressure = self.current_load / self.max_parallel
        if pressure > 0.8:
            return 2
        elif pressure > 0.5:
            return 5
        return self.max_parallel

    def register_load(self):
        self.current_load += 1

    def release_load(self):
        self.current_load = max(0, self.current_load - 1)
```

---

## Integration Points

### LLM Provider Chain

```
User Request → CircuitBreaker(HuggingFace) → RetryPolicy → HF API
                    ↓ (OPEN)
               CircuitBreaker(OpenAI)      → RetryPolicy → OpenAI API
                    ↓ (OPEN)
               AllProvidersUnavailable
```

### Orchestrator Pipeline

```
Request
  → RequestQueueLimiter.acquire()
  → TimeoutController.execute(pipeline, timeout=30)
    → AgentExecutionLimiter.execute(agent, ...)
    → ResponseValidator.validate(response)
  → RequestQueueLimiter.release()
```

### Metrics Integration

Every reliability component emits Prometheus metrics:

| Metric | Type | Component |
|--------|------|-----------|
| `circuit_breaker_state` | Gauge | CircuitBreaker |
| `circuit_breaker_failures_total` | Counter | CircuitBreaker |
| `retry_attempts_total` | Counter | RetryPolicy |
| `timeout_exceeded_total` | Counter | TimeoutController |
| `response_validation_errors_total` | Counter | ResponseValidator |
| `request_queue_size` | Gauge | RequestQueueLimiter |
| `agent_execution_concurrent` | Gauge | AgentExecutionLimiter |

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| CB failure threshold | 3 | Failures before circuit opens |
| CB recovery timeout | 60s | Time before half-open attempt |
| Retry max attempts | 3 | Maximum retry count |
| Retry base delay | 1.0s | Initial retry delay |
| Retry max delay | 30s | Maximum retry delay |
| Timeout default | 30s | Default operation timeout |
| Request queue max | 100 | Maximum concurrent requests |
| Agent concurrency max | 20 | Maximum concurrent agents |
| Swarm max parallel | 10 | Maximum swarm parallelism |
| Response max length | 4096 chars | Response truncation limit |
| Failure window size | 100 | Failure tracker deque size |

---

## Failure Modes

| Component | Failure | Impact | Recovery |
|-----------|---------|--------|----------|
| CircuitBreaker | OPEN state | Calls rejected | Recovery after timeout |
| RetryPolicy | Max retries exhausted | Operation fails | Surface error to caller |
| TimeoutController | Timeout exceeded | Operation cancelled | Fallback function if configured |
| FailureTracker | Thread contention | Metric delay | Lock contention is brief |
| ResponseValidator | False positive injection | Over-sanitized response | Tune detection patterns |
| RequestQueueLimiter | Semaphore exhausted | 429 Too Many Requests | Client retries |
| AgentExecutionLimiter | Semaphore exhausted | Agent queued | Watchdog may timeout |
| SwarmThrottle | High pressure | Reduced parallelism | Auto-adjusts as load drops |
