"""Runtime configuration.

Every external endpoint is URL-referenced so the corpus/LLM tiers are swappable
(this is the KAITO lock-in escape hatch from the spec, §3.1). Defaults point at the
homelab's host Ollama; set TUBEWIKI_OFFLINE=1 to run the deterministic stub path
with no GPU.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (backend/tubewiki/config.py -> repo/)
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TUBEWIKI_", env_file=".env", extra="ignore")

    # --- Storage locations -------------------------------------------------
    data_dir: Path = Field(default=_REPO_ROOT / "data")
    vault_dir: Path = Field(default=_REPO_ROOT / "vault")
    qdrant_path: Path = Field(default=_REPO_ROOT / "data" / "qdrant")
    ledger_path: Path = Field(default=_REPO_ROOT / "data" / "ledger.json")

    # --- Ollama (host service on the GPU box) ------------------------------
    # LLM uses the OpenAI-compatible /v1 path; embeddings use the NATIVE /api/embed.
    ollama_base_url: str = "http://192.168.86.11:11434"
    llm_model: str = "qwen2.5:14b"
    embedding_model: str = "qwen3-embedding"
    # qwen3-embedding is 2560-dim on this box (verified); /api/embed would otherwise
    # be mis-assumed as 3072. Keep this in sync with the chosen model.
    embedding_dim: int = 2560

    # --- Behaviour ---------------------------------------------------------
    # Offline = deterministic stub embedder + template LLM. Auto-forced on if the
    # box is unreachable at startup (see clients.py).
    offline: bool = False
    collection_name: str = "tubewiki"
    chunk_chars: int = 1200
    chunk_overlap: int = 150
    retrieval_top_k: int = 6

    # --- API ---------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.qdrant_path.mkdir(parents=True, exist_ok=True)
        self.vault_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
