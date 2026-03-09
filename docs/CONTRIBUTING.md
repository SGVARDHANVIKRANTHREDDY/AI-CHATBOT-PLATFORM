# Contributing

> Guidelines for contributing to the AI Chatbot Platform — code style, branching strategy, PR workflow, testing requirements, and CI pipeline.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Branching Strategy](#branching-strategy)
- [Code Style](#code-style)
- [Testing Requirements](#testing-requirements)
- [Pull Request Workflow](#pull-request-workflow)
- [CI Pipeline](#ci-pipeline)
- [Commit Messages](#commit-messages)
- [Code Review Checklist](#code-review-checklist)
- [Release Process](#release-process)

---

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/<your-username>/chatbot.git
   cd chatbot
   ```
3. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   .venv\Scripts\activate     # Windows
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install ruff mypy bandit pytest pytest-asyncio pytest-cov
   ```
5. Verify setup:
   ```bash
   ruff check .
   pytest tests/unit/ -v
   ```

---

## Development Setup

See [INSTALLATION.md](INSTALLATION.md) for full environment setup including Redis, PostgreSQL, and API keys.

### Minimum Requirements

| Tool | Version |
|------|---------|
| Python | 3.12+ |
| Redis | 5+ (for integration tests) |
| Git | 2.30+ |

---

## Branching Strategy

```
main (production)
├── develop (integration)
│   ├── feature/agent-memory-v2
│   ├── feature/qdrant-migration
│   ├── fix/cache-invalidation
│   └── fix/circuit-breaker-race
```

| Branch | Purpose | Merges Into |
|--------|---------|-------------|
| `main` | Production-ready code | — |
| `develop` | Integration branch | `main` via PR |
| `feature/*` | New features | `develop` via PR |
| `fix/*` | Bug fixes | `develop` via PR |
| `hotfix/*` | Critical production fixes | `main` + `develop` |

### Rules

- Never push directly to `main` or `develop`
- All changes go through pull requests
- `main` requires passing CI and at least 1 approval
- `develop` requires passing CI

---

## Code Style

### Linting: Ruff

Configuration in `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py312"
line-length = 120
src = ["app", "workers", "tests"]
```

#### Enabled Rule Sets

| Code | Rule Set | Purpose |
|------|----------|---------|
| `E` | pycodestyle errors | Basic style errors |
| `W` | pycodestyle warnings | Style warnings |
| `F` | pyflakes | Unused imports, undefined names |
| `I` | isort | Import sorting |
| `N` | pep8-naming | Naming conventions |
| `UP` | pyupgrade | Python version upgrades |
| `B` | flake8-bugbear | Common bugs |
| `S` | flake8-bandit | Security issues |
| `T20` | flake8-print | Print statement detection |
| `SIM` | flake8-simplify | Code simplification |
| `RUF` | ruff-specific | Ruff custom rules |

### Formatting

```bash
# Check formatting
ruff format --check .

# Auto-format
ruff format .
```

- Quote style: double quotes
- Line length: 120 characters
- Import sorting: isort-compatible (via `I` rule)

### Type Checking: mypy

```bash
mypy app/ --ignore-missing-imports
```

Configuration:
```toml
[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
ignore_missing_imports = true
```

### Security Scanning: bandit

```bash
bandit -r app/ -c pyproject.toml
```

Excluded directories: `tests`, `.venv`, `node_modules`

---

## Testing Requirements

### Before Submitting a PR

| Test Type | Command | Required |
|-----------|---------|----------|
| Unit tests | `pytest tests/unit/ -v` | Yes |
| Integration tests | `pytest tests/integration/ -v` | For backend changes |
| E2E tests | `pytest tests/e2e/ -v` | For API changes |
| Linting | `ruff check .` | Yes |
| Formatting | `ruff format --check .` | Yes |

### Coverage Requirements

- New code should include tests
- Aim for ≥ 80% coverage on new modules
- Run coverage report:
  ```bash
  pytest --cov=app --cov-report=term-missing tests/
  ```

### Test Organization

- Place unit tests in `tests/unit/test_<module>.py`
- Place integration tests in `tests/integration/test_<feature>.py`
- Place E2E tests in `tests/e2e/test_<scenario>.py`
- Use `conftest.py` for shared fixtures at each level

---

## Pull Request Workflow

### 1. Create Branch

```bash
git checkout develop
git pull origin develop
git checkout -b feature/my-feature
```

### 2. Make Changes

- Write code following style guidelines
- Add tests for new functionality
- Update documentation if needed

### 3. Validate Locally

```bash
ruff check .
ruff format --check .
pytest tests/unit/ -v
pytest tests/integration/ -v  # if applicable
```

### 4. Push and Create PR

```bash
git push origin feature/my-feature
```

Create a PR targeting `develop` with:
- **Title:** Clear, concise description of the change
- **Description:** What changed and why
- **Testing:** How the change was tested
- **Breaking changes:** Any backwards-incompatible changes

### 5. CI Checks

All 9 CI stages must pass:
1. Lint (ruff)
2. Type check (mypy)
3. Security scan (bandit)
4. Dependency audit (pip-audit)
5. Unit tests
6. Integration tests
7. E2E tests
8. Docker build (push only)
9. Staging deploy (main only)

### 6. Review and Merge

- At least 1 approval required
- Address all review comments
- Squash-merge to keep a clean history

---

## CI Pipeline

The CI/CD pipeline runs automatically on:
- **Push** to `main` or `develop`
- **Pull requests** targeting `main`

See [DEPLOYMENT.md](DEPLOYMENT.md) for full pipeline details.

### Concurrency

Superseded runs on the same branch or PR are automatically cancelled to save CI minutes.

---

## Commit Messages

### Format

```
<type>: <short description>

<optional body>
```

### Types

| Type | Use For |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `refactor` | Code restructuring |
| `test` | Adding/modifying tests |
| `ci` | CI/CD changes |
| `chore` | Maintenance tasks |

### Examples

```
feat: add Qdrant vector store provider
fix: circuit breaker race condition in half-open state
docs: add SCALING.md deployment guide
test: add chaos tests for cascading LLM failures
refactor: extract token budgeting into dedicated module
```

---

## Code Review Checklist

- [ ] Code follows project style (ruff clean)
- [ ] Tests pass locally
- [ ] New code has test coverage
- [ ] No security issues (bandit clean)
- [ ] Error handling follows patterns in [ERROR_HANDLING.md](ERROR_HANDLING.md)
- [ ] External calls have timeouts and circuit breakers
- [ ] No hardcoded secrets or credentials
- [ ] Documentation updated for API changes
- [ ] No unnecessary dependencies added

---

## Release Process

1. Merge `develop` → `main` via PR
2. CI pipeline builds Docker image and pushes to ghcr.io
3. Automatic staging deployment
4. Manual verification of staging
5. Tag the release: `git tag v1.x.x && git push --tags`
6. Deploy to production

---

## Contributing Documentation

Documentation lives in `docs/` and follows these conventions:

### File Structure

Every doc file should include:
1. **Title** (`# Section Name`)
2. **One-line description** in a blockquote (`> What this doc covers`)
3. **Table of Contents** with anchor links
4. **Content sections** with code examples where applicable

### How to Contribute Docs

```bash
# 1. Create a branch
git checkout -b docs/improve-rag-pipeline

# 2. Edit the doc in docs/
# Follow existing formatting patterns

# 3. Validate markdown links
# Ensure all [links](FILE.md) point to existing files

# 4. Submit PR targeting develop
# No CI tests run for docs-only changes, but review is required
```

### Documentation Style Guide

| Do | Don't |
|----|-------|
| Use tables for configuration reference | Write configuration as prose paragraphs |
| Show complete code examples that can be copied | Show fragments without imports or context |
| Include ASCII diagrams for architecture | Describe architecture in text only |
| Link to other docs with `[name](FILE.md)` | Say "see the other file" without linking |
| Explain *why* a default was chosen | Just list defaults without rationale |
| Show error messages and how to fix them | Say "handle errors appropriately" |

### Example: Good Documentation PR

A good documentation PR should:
1. **Fix one specific gap** — e.g., "Add streaming examples to API_REFERENCE.md"
2. **Include before/after** — Show what section was incomplete and what was added
3. **Test code examples** — Verify that all code snippets actually work
4. **Cross-link** — Add links to/from related docs
5. **Keep formatting consistent** — Match the style of surrounding sections
