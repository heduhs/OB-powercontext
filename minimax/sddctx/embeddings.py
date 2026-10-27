"""Embedding functions for sddctx.

Primary provider is the MiniMax embedding API (embo-01, 1536 dims), which
handles the Chinese spec text well. When no MiniMax API key is configured the
module falls back to pyseekdb's local ONNX model (all-MiniLM-L6-v2, 384 dims)
so that the system remains runnable offline. The active provider is recorded
in the collection config; mixing providers in one collection is rejected.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Optional

import pyseekdb

from .config import settings

logger = logging.getLogger(__name__)

MINIMAX_DIM = 1536
LOCAL_ONNX_DIM = 384


class MiniMaxEmbeddingError(RuntimeError):
    pass


def _minimax_embed(texts: list[str], api_key: str, base_url: str) -> list[list[float]]:
    url = base_url.rstrip("/") + "/embeddings"
    payload = json.dumps({"model": "embo-01", "texts": texts, "type": "db"}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_exc: Optional[Exception] = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode())
            # embo-01 returns embeddings under "vectors"; some MiniMax SDK
            # paths use "embeddings". Accept either.
            vectors = body.get("vectors") or body.get("embeddings")
            if vectors is None:
                status = (body.get("base_resp") or {}).get("status_msg")
                raise MiniMaxEmbeddingError(
                    f"unexpected MiniMax response: {status or body}"
                )
            return vectors
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("MiniMax embedding attempt %d failed: %s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    raise MiniMaxEmbeddingError(f"MiniMax embedding failed after retries: {last_exc}")


def minimax_available() -> bool:
    if not settings.minimax_api_key:
        return False
    try:
        _minimax_embed(
            ["ping"], settings.minimax_api_key, settings.minimax_base_url
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("MiniMax embedding unavailable, falling back to local ONNX: %s", exc)
        return False


@pyseekdb.register_embedding_function
class SddEmbeddingFunction:
    """Routes to MiniMax or the local ONNX model; provider is fixed per instance."""

    def __init__(self, provider: str = "local") -> None:
        if provider not in ("minimax", "local"):
            raise ValueError(f"unknown embedding provider: {provider}")
        self.provider = provider
        self._local = pyseekdb.DefaultEmbeddingFunction() if provider == "local" else None

    def __call__(self, documents) -> list[list[float]]:
        if isinstance(documents, str):
            documents = [documents]
        if self.provider == "minimax":
            out: list[list[float]] = []
            # embo-01 accepts batches; chunk to stay well within limits
            for i in range(0, len(documents), 32):
                out.extend(
                    _minimax_embed(
                        list(documents[i : i + 32]),
                        settings.minimax_api_key,
                        settings.minimax_base_url,
                    )
                )
            return out
        return self._local(list(documents))

    @property
    def dimension(self) -> int:
        return MINIMAX_DIM if self.provider == "minimax" else LOCAL_ONNX_DIM

    @staticmethod
    def name() -> str:
        return "sdd_embedding"

    def get_config(self) -> dict[str, Any]:
        return {"provider": self.provider}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "SddEmbeddingFunction":
        return SddEmbeddingFunction(provider=config.get("provider", "local"))


def resolve_provider() -> str:
    """Pick the embedding provider once per process, honoring SDD_EMBEDDING_MODE."""
    mode = settings.embedding_mode
    if mode == "local":
        return "local"
    if mode == "minimax":
        return "minimax"
    return "minimax" if minimax_available() else "local"
