"""Configuration for the SDD context module."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    # seekdb (MySQL protocol)
    seekdb_host: str = field(
        default_factory=lambda: os.environ.get("SDD_SEEKDB_HOST", "127.0.0.1")
    )
    seekdb_port: int = field(
        default_factory=lambda: int(os.environ.get("SDD_SEEKDB_PORT", "2881"))
    )
    seekdb_tenant: str = field(
        default_factory=lambda: os.environ.get("SDD_SEEKDB_TENANT", "sys")
    )
    seekdb_user: str = field(
        default_factory=lambda: os.environ.get("SDD_SEEKDB_USER", "root")
    )
    seekdb_password: str = field(
        default_factory=lambda: os.environ.get("SDD_SEEKDB_PASSWORD", "")
    )
    seekdb_database: str = field(
        default_factory=lambda: os.environ.get("SDD_SEEKDB_DATABASE", "sddctx")
    )

    # MiniMax API
    minimax_api_key: str = field(
        default_factory=lambda: os.environ.get("MINIMAX_API_KEY", "")
    )
    minimax_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"
        )
    )
    minimax_model: str = field(
        default_factory=lambda: os.environ.get("MINIMAX_MODEL", "MiniMax-M1")
    )

    # PowerContext
    powercontext_url: str = field(
        default_factory=lambda: os.environ.get(
            "POWERCONTEXT_URL", "http://127.0.0.1:8000"
        )
    )
    powercontext_scope: str = field(
        default_factory=lambda: os.environ.get("POWERCONTEXT_SCOPE", "sdd-demo")
    )

    # Embedding mode: "auto" uses MiniMax embedding when an API key is set,
    # otherwise falls back to the local ONNX model shipped with pyseekdb.
    embedding_mode: str = field(
        default_factory=lambda: os.environ.get("SDD_EMBEDDING_MODE", "auto")
    )

    project_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("SDD_PROJECT_ROOT", str(Path(__file__).resolve().parent.parent / "demo_project"))
        )
    )


settings = Settings()
