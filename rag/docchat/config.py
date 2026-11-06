"""Settings, read from the environment and rag/.env."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAG_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    """Return an env var, treating an empty value as unset."""
    return os.getenv(name, "").strip() or default


def _path(value: str) -> Path:
    """Resolve a path relative to rag/, whatever the current folder is."""
    return (RAG_ROOT / value).resolve()


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    site_root: Path
    content_dirs: tuple[str, ...]
    docs_route_base: str
    site_base_url: str
    public_site_url: str
    chroma_dir: Path
    embed_model: str
    embed_cache_dir: Path
    chunk_size: int
    chunk_overlap: int
    top_k: int
    min_score: float | None
    claude_model: str
    allowed_origins: tuple[str, ...]
    # repr=False keeps the key out of logs and tracebacks.
    anthropic_api_key: str | None = field(default=None, repr=False)


def load_settings() -> Settings:
    """Build Settings. Real env vars win over values in rag/.env."""
    load_dotenv(RAG_ROOT / ".env")
    min_score = _env("MIN_SCORE", "")
    return Settings(
        site_root=_path(_env("SITE_ROOT", "..")),
        content_dirs=_csv(_env("CONTENT_DIRS", "docs")),
        docs_route_base=_env("DOCS_ROUTE_BASE", "/docs"),
        site_base_url=_env("SITE_BASE_URL", "/"),
        public_site_url=_env("PUBLIC_SITE_URL", "http://localhost:3000"),
        chroma_dir=_path(_env("CHROMA_DIR", "data/chroma")),
        embed_model=_env(
            "EMBED_MODEL",
            "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        ),
        embed_cache_dir=_path(_env("EMBED_CACHE_DIR", "data/models")),
        chunk_size=int(_env("CHUNK_SIZE", "800")),
        chunk_overlap=int(_env("CHUNK_OVERLAP", "150")),
        top_k=int(_env("TOP_K", "5")),
        min_score=float(min_score) if min_score else None,
        claude_model=_env("CLAUDE_MODEL", "claude-sonnet-5-5"),
        allowed_origins=_csv(_env("ALLOWED_ORIGINS", "http://localhost:3000")),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", "") or None,
    )
