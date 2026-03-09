# Nimbus — AI Chatbot Platform

> A production-grade, privacy-first, multi-agent AI assistant with RAG retrieval, knowledge graphs, semantic caching, prompt evolution, and enterprise reliability primitives.

[![CI/CD Pipeline](https://github.com/your-org/chatbot/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/your-org/chatbot/actions/workflows/ci-cd.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Table of Contents

- [Project Description](#project-description)
- [Key Features](#key-features)
- [Architecture Overview](#architecture-overview)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage Examples](#usage-examples)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## Project Description

Nimbus is a self-hosted, privacy-first AI chatbot platform designed for production workloads. It orchestrates multiple specialized AI agents across a directed acyclic graph (DAG) of reasoning steps, backed by retrieval-augmented generation (RAG), a persistent knowledge graph, multi-tier vector memory, and a comprehensive reliability layer.

The platform is built on **FastAPI** with **Python 3.12**, supports **HuggingFace** and **OpenAI** LLM providers with automatic fallback, and is designed for horizontal scaling via **Redis**, **PostgreSQL**, **Qdrant**, and **Celery** workers.

### Core Design Philosophy

| Principle | Implementation |
|-----------|---------------|
| **Privacy-First** | Local-first operation; web search is optional and explicitly enabled per-request |
| **Grounded Responses** | RAG enforcement with configurable minimum confidence; refusal when unsure |
| **Multi-Agent Intelligence** | DAG-based task decomposition with specialized agents (research, coding, reasoning, critic) |
| **Production Hardening** | Circuit breakers, retry policies, timeout controllers, load guards, watchdogs |
| **Observable** | 80+ Prometheus metrics, OpenTelemetry tracing, ELK-compatible JSON logging |
| **Extensible** | Plugin system with subprocess isolation, tool registry, neural tool routing |

---

## Key Features

### Intelligence Layer
- **Multi-Agent Orchestration** — Planner decomposes queries into DAGs; specialized agents execute in parallel or sequentially
- **Agent Swarm Execution** — Parallel agent spawning with configurable concurrency limits
- **Reasoning Graph Engine** — DAG-based multi-step reasoning with dependency resolution and depth limits
- **Critic Agent** — Self-evaluation for hallucination detection and quality gating
- **Prompt Evolution** — Automated A/B testing and LLM-driven prompt mutation
- **Response Evaluation** — LLM-as-a-judge scoring with dataset collection for continuous improvement

### Retrieval & Knowledge
- **RAG Pipeline** — Document ingestion, sentence-based chunking, FAISS/Qdrant vector indexing, cross-encoder reranking
- **Knowledge Graph** — LLM-based entity extraction with trust-scored relationship storage
- **Semantic Cache** — Redis-backed response caching with cosine similarity matching (92% threshold)
- **Multi-Tier Vector Memory** — Episodic, semantic, and profile memory with pluggable backends

### Production Infrastructure
- **Reliability Layer** — Three-state circuit breaker, exponential backoff with jitter, timeout controllers
- **Security Model** — JWT authentication, API key validation, prompt injection detection, content safety scoring
- **Observability** — Prometheus metrics, OpenTelemetry distributed tracing, Filebeat log shipping
- **Background Workers** — Celery-based document ingestion, knowledge crawling, vector maintenance

---

## Architecture Overview

```
User Request
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│                    API Layer (FastAPI)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ JWT Auth │ │Rate Limit│ │ CORS     │ │ Correlation│ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                  Chat Orchestrator                       │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐ │
│  │Semantic Cache│  │Prompt Security│  │Response Guard│ │
│  └──────────────┘  └───────────────┘  └──────────────┘ │
└───────────────────────┬─────────────────────────────────┘
                        │
              ┌─────────┴──────────┐
              ▼                    ▼
┌──────────────────┐    ┌──────────────────┐
│  Planner Agent   │    │  Context Builder  │
│  (DAG Generator) │    │  (RAG + Memory)   │
└────────┬─────────┘    └──────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│              Reasoning Graph Engine                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ Research │ │ Coding   │ │Reasoning │ │ Tool Calls │ │
│  │ Agent   │ │ Agent    │ │ Agent    │ │            │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
└───────────────────────┬─────────────────────────────────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
┌──────────────┐ ┌────────────┐ ┌────────────────┐
│  Tool Router │ │ RAG        │ │ Knowledge      │
│  (Neural)    │ │ Retriever  │ │ Graph          │
└──────────────┘ └────────────┘ └────────────────┘
         │              │              │
         ▼              ▼              ▼
┌──────────────┐ ┌────────────┐ ┌────────────────┐
│  Plugin      │ │ Vector     │ │ Graph Store    │
│  Sandbox     │ │ Index      │ │ (JSON)         │
└──────────────┘ └────────────┘ └────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                   Critic Agent                          │
│  Self-evaluation → Hallucination check → Quality gate   │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
                   Final Response
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- Redis 7+
- PostgreSQL 15+ (optional for persistent memory)

### 1. Clone and Install

```bash
git clone https://github.com/your-org/chatbot.git
cd chatbot
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings:
#   HF_TOKEN=your_huggingface_token
#   REDIS_URL=redis://localhost:6379/0
```

### 3. Start Services

```bash
# Start Redis
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Start the API server
python run.py
```

### 4. Send a Request

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What is retrieval-augmented generation?"}'
```

### Docker Quick Start

```bash
cd infra/docker
docker compose up -d
```

---

## Installation

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed setup instructions covering local development, Docker, and production deployments.

---

## Usage Examples

### Basic Chat

```python
import httpx

async def chat():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/v1/chat",
            json={
                "question": "Explain the circuit breaker pattern",
                "use_rag": True,
                "use_web": False,
                "session_id": "user-123"
            }
        )
        data = response.json()
        print(data["result"]["answer"])
        print(f"Confidence: {data['result']['confidence']}")
```

### Streaming Chat

```python
import httpx

async def stream_chat():
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            "http://localhost:8000/api/v1/chat/stream",
            json={"question": "Write a Python decorator for retries"}
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    print(line[6:], end="", flush=True)
```

---

## Documentation

Full documentation is available in the [docs/](docs/) directory:

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, layers, data flow, design decisions |
| [SYSTEM_OVERVIEW.md](docs/SYSTEM_OVERVIEW.md) | High-level system purpose, components, and interactions |
| [INSTALLATION.md](docs/INSTALLATION.md) | Setup instructions for all environments |
| [QUICKSTART.md](docs/QUICKSTART.md) | Get running in 5 minutes |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | All environment variables and settings |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | Complete API endpoint documentation |
| [AGENT_SYSTEM.md](docs/AGENT_SYSTEM.md) | Multi-agent orchestration and reasoning |
| [MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md) | Vector memory, conversation store, authority model |
| [RAG_PIPELINE.md](docs/RAG_PIPELINE.md) | Retrieval-augmented generation pipeline |
| [KNOWLEDGE_GRAPH.md](docs/KNOWLEDGE_GRAPH.md) | Entity extraction, graph storage, trust scoring |
| [TOOL_ROUTER.md](docs/TOOL_ROUTER.md) | Neural tool selection and tool registry |
| [PLUGIN_SYSTEM.md](docs/PLUGIN_SYSTEM.md) | Plugin discovery, isolation, and execution |
| [VECTOR_DATABASE.md](docs/VECTOR_DATABASE.md) | FAISS and Qdrant backends, migration |
| [PROMPT_EVOLUTION.md](docs/PROMPT_EVOLUTION.md) | Automated prompt versioning and A/B testing |
| [SWARM_EXECUTION.md](docs/SWARM_EXECUTION.md) | Parallel agent execution strategies |
| [RELIABILITY_LAYER.md](docs/RELIABILITY_LAYER.md) | Circuit breakers, retries, timeouts, load guards |
| [SECURITY_MODEL.md](docs/SECURITY_MODEL.md) | Authentication, injection defense, content safety |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | Metrics, tracing, logging, dashboards |
| [TESTING.md](docs/TESTING.md) | Test structure, running tests, chaos testing |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, CI/CD, production deployment |
| [SCALING.md](docs/SCALING.md) | Horizontal scaling strategies |
| [PERFORMANCE.md](docs/PERFORMANCE.md) | Bottlenecks, caching, optimization |
| [ERROR_HANDLING.md](docs/ERROR_HANDLING.md) | Exception hierarchy, failure modes, recovery |
| [CONTRIBUTING.md](docs/CONTRIBUTING.md) | How to contribute to the project |
| [DEVELOPMENT_GUIDE.md](docs/DEVELOPMENT_GUIDE.md) | Developer onboarding and extension guide |
| [DIRECTORY_STRUCTURE.md](docs/DIRECTORY_STRUCTURE.md) | Complete repository layout explanation |
| [CODE_WALKTHROUGH.md](docs/CODE_WALKTHROUGH.md) | Module-by-module code documentation |

---

## Contributing

We welcome contributions. Please read [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for the workflow, coding standards, and testing requirements.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
