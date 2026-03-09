from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Base Paths
    BASE_DIR: Path = Field(default=Path(__file__).resolve().parent.parent.parent)
    DATA_DIR: Path = Field(default=Path(__file__).resolve().parent.parent.parent / "data")

    # Storage Paths
    UPLOADED_DOCS_DIR: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "raw_docs"
    )
    PROCESSED_DOCS_DIR: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "processed_docs"
    )
    VECTOR_INDEX_DIR: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "vector_index"
    )

    # LLM Configuration (Primary: HuggingFace)
    HF_TOKEN: str | None = Field(default=None, json_schema_extra={"env": "HF_TOKEN"})
    HF_MODEL: str = Field(default="mistralai/Mistral-7B-Instruct-v0.2", json_schema_extra={"env": "HF_MODEL"})
    HF_API_URL: str | None = Field(default=None, json_schema_extra={"env": "HF_API_URL"})
    HF_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=1.0, json_schema_extra={"env": "HF_TEMPERATURE"})
    HF_MAX_TOKENS: int = Field(default=1024, gt=0, json_schema_extra={"env": "HF_MAX_TOKENS"})

    # LLM Configuration (Fallback: OpenAI)
    OPENAI_API_KEY: str | None = Field(default=None, json_schema_extra={"env": "OPENAI_API_KEY"})
    OPENAI_MODEL: str = Field(default="gpt-4-turbo-preview", json_schema_extra={"env": "OPENAI_MODEL"})
    OPENAI_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=1.0, json_schema_extra={"env": "OPENAI_TEMPERATURE"})

    # Global LLM settings
    LLM_RETRY_ATTEMPTS: int = Field(default=3, ge=0, json_schema_extra={"env": "LLM_RETRY_ATTEMPTS"})
    LLM_TIMEOUT: int = Field(default=60, gt=0, json_schema_extra={"env": "LLM_TIMEOUT"})

    # RAG Configuration
    EMBEDDING_MODEL: str = Field(default="all-MiniLM-L6-v2", json_schema_extra={"env": "EMBEDDING_MODEL"})
    RAG_CHUNK_MAX_WORDS: int = Field(default=220, gt=0, json_schema_extra={"env": "RAG_CHUNK_MAX_WORDS"})
    RAG_CHUNK_OVERLAP_SENTENCES: int = Field(default=2, ge=0, json_schema_extra={"env": "RAG_CHUNK_OVERLAP_SENTENCES"})
    RAG_TOP_K: int = Field(default=3, gt=0, json_schema_extra={"env": "RAG_TOP_K"})
    RAG_ENFORCE_GROUNDING: bool = Field(default=True, json_schema_extra={"env": "RAG_ENFORCE_GROUNDING"})
    RAG_MIN_SCORE: float = Field(default=0.35, ge=0.0, le=1.0, json_schema_extra={"env": "RAG_MIN_SCORE"})
    RAG_REFUSAL_MESSAGE: str = Field(
        default="I don't have enough reliable information to answer this question.",
        json_schema_extra={"env": "RAG_REFUSAL_MESSAGE"},
    )

    # Memory Configuration
    REDIS_URL: str = Field(default="redis://localhost:6379/0", json_schema_extra={"env": "REDIS_URL"})
    POSTGRES_URL: str = Field(
        default="postgresql://user:password@localhost:5432/chatbot", json_schema_extra={"env": "POSTGRES_URL"}
    )

    @field_validator("REDIS_URL")
    @classmethod
    def validate_redis_url(cls, v: str) -> str:
        if not v.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must start with redis:// or rediss://")
        return v

    @field_validator("POSTGRES_URL")
    @classmethod
    def validate_postgres_url(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("POSTGRES_URL must start with postgresql:// or postgres://")
        return v

    MEMORY_MAX_TURNS: int = Field(default=20, json_schema_extra={"env": "MEMORY_MAX_TURNS"})
    MEMORY_SUMMARY_EVERY_N_TURNS: int = Field(default=12, json_schema_extra={"env": "MEMORY_SUMMARY_EVERY_N_TURNS"})
    MEMORY_CONTEXT_MAX_CHARS: int = Field(default=8000, json_schema_extra={"env": "MEMORY_CONTEXT_MAX_CHARS"})
    MEMORY_CONTEXT_TAIL_TURNS: int = Field(default=6, json_schema_extra={"env": "MEMORY_CONTEXT_TAIL_TURNS"})

    # Web Search
    WEB_MAX_RESULTS: int = Field(default=5, json_schema_extra={"env": "WEB_MAX_RESULTS"})
    WEB_PAGE_MAX_CHARS: int = Field(default=1800, json_schema_extra={"env": "WEB_PAGE_MAX_CHARS"})
    WEB_CONTEXT_MAX_CHARS: int = Field(default=4000, json_schema_extra={"env": "WEB_CONTEXT_MAX_CHARS"})

    # API Configuration
    API_HOST: str = Field(default="0.0.0.0", json_schema_extra={"env": "API_HOST"})
    API_PORT: int = Field(default=8000, json_schema_extra={"env": "API_PORT"})
    API_RATE_LIMIT: int = Field(default=100, json_schema_extra={"env": "API_RATE_LIMIT"})
    API_AGENT_RATE_LIMIT: int = Field(default=10, json_schema_extra={"env": "API_AGENT_RATE_LIMIT"})
    DEBUG: bool = Field(default=False, json_schema_extra={"env": "DEBUG"})

    # JWT Authentication
    JWT_SECRET_KEY: str = Field(default="", json_schema_extra={"env": "JWT_SECRET_KEY"})
    JWT_ALGORITHM: str = Field(default="HS256", json_schema_extra={"env": "JWT_ALGORITHM"})
    JWT_EXPIRY_MINS: int = Field(default=60, gt=0, json_schema_extra={"env": "JWT_EXPIRY_MINS"})

    # Request Protection
    MAX_REQUEST_BODY_BYTES: int = Field(
        default=1048576, gt=0, json_schema_extra={"env": "MAX_REQUEST_BODY_BYTES"}
    )  # 1 MB
    REQUEST_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0, json_schema_extra={"env": "REQUEST_TIMEOUT_SECONDS"})

    # Content Safety
    CONTENT_SAFETY_ENABLED: bool = Field(default=True, json_schema_extra={"env": "CONTENT_SAFETY_ENABLED"})
    CONTENT_SAFETY_INJECTION_THRESHOLD: float = Field(
        default=0.6, ge=0.0, le=1.0, json_schema_extra={"env": "CONTENT_SAFETY_INJECTION_THRESHOLD"}
    )
    CONTENT_SAFETY_QUARANTINE_THRESHOLD: float = Field(
        default=0.35, ge=0.0, le=1.0, json_schema_extra={"env": "CONTENT_SAFETY_QUARANTINE_THRESHOLD"}
    )
    CONTENT_SAFETY_QUALITY_FLOOR: float = Field(
        default=0.3, ge=0.0, le=1.0, json_schema_extra={"env": "CONTENT_SAFETY_QUALITY_FLOOR"}
    )

    # Performance Layer
    SEMANTIC_CACHE_ENABLED: bool = Field(default=True, json_schema_extra={"env": "SEMANTIC_CACHE_ENABLED"})
    SEMANTIC_CACHE_THRESHOLD: float = Field(
        default=0.92, ge=0.0, le=1.0, json_schema_extra={"env": "SEMANTIC_CACHE_THRESHOLD"}
    )
    SEMANTIC_CACHE_TTL: int = Field(default=3600 * 24, json_schema_extra={"env": "SEMANTIC_CACHE_TTL"})  # 24 hours
    SEMANTIC_CACHE_MAX_ENTRIES: int = Field(default=1000, gt=0, json_schema_extra={"env": "SEMANTIC_CACHE_MAX_ENTRIES"})

    RERANKING_ENABLED: bool = Field(default=True, json_schema_extra={"env": "RERANKING_ENABLED"})
    RERANKER_MODEL: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2", json_schema_extra={"env": "RERANKER_MODEL"}
    )
    RERANKER_TOP_K: int = Field(default=5, gt=0, json_schema_extra={"env": "RERANKER_TOP_K"})
    RERANKER_CANDIDATES: int = Field(default=20, gt=0, json_schema_extra={"env": "RERANKER_CANDIDATES"})

    UX_TOKEN_LIMIT: int = Field(default=4096, gt=0, json_schema_extra={"env": "UX_TOKEN_LIMIT"})

    # Agent Brain Layer - Specialized Models
    MODEL_REASONING: str = Field(
        default="mistralai/Mistral-7B-Instruct-v0.2", json_schema_extra={"env": "MODEL_REASONING"}
    )
    MODEL_CODING: str = Field(default="codellama/CodeLlama-13b-Instruct-hf", json_schema_extra={"env": "MODEL_CODING"})
    MODEL_SUMMARIZATION: str = Field(
        default="sshleifer/distilbart-cnn-12-6", json_schema_extra={"env": "MODEL_SUMMARIZATION"}
    )

    # Vector Memory Settings
    VECTOR_MEMORY_ENABLED: bool = Field(default=True, json_schema_extra={"env": "VECTOR_MEMORY_ENABLED"})
    VECTOR_MEMORY_DIM: int = Field(default=384, json_schema_extra={"env": "VECTOR_MEMORY_DIM"})
    VECTOR_MEMORY_TOP_K: int = Field(default=5, json_schema_extra={"env": "VECTOR_MEMORY_TOP_K"})

    # Vector Backend: "faiss" (dev) or "qdrant" (production)
    VECTOR_BACKEND: str = Field(default="faiss", json_schema_extra={"env": "VECTOR_BACKEND"})
    QDRANT_URL: str = Field(default="http://localhost:6333", json_schema_extra={"env": "QDRANT_URL"})
    QDRANT_API_KEY: str | None = Field(default=None, json_schema_extra={"env": "QDRANT_API_KEY"})

    # UX / Branding
    ASSISTANT_NAME: str = Field(default="Nimbus", json_schema_extra={"env": "ASSISTANT_NAME"})
    DEFAULT_SYSTEM_PROMPT: str = Field(
        default=(
            "You are Nimbus, a privacy-first local AI assistant.\n"
            "Core guarantees:\n"
            "- Offline-first and privacy-preserving (local by default; web search is optional).\n"
            "- Grounded: do not hallucinate facts. If unsure, say so.\n"
            "- Citation-aware: when sources are provided, prefer them and be explicit about uncertainty.\n"
            "- Follow the user's instructions unless they conflict with these guarantees.\n"
        )
    )

    def ensure_dirs(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOADED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
        self.PROCESSED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
        self.VECTOR_INDEX_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
