"""ZhiYan (智研) configuration.

All secrets come from environment variables; nothing sensitive is committed.
"""

from __future__ import annotations

import os


class Settings:
    # seekdb (MySQL protocol) — hybrid retrieval over literature & notes
    SEEKDB_HOST = os.environ.get("SEEKDB_HOST", "127.0.0.1")
    SEEKDB_PORT = int(os.environ.get("SEEKDB_PORT", "2881"))
    SEEKDB_TENANT = os.environ.get("SEEKDB_TENANT", "sys")
    SEEKDB_USER = os.environ.get("SEEKDB_USER", "root")
    SEEKDB_PASSWORD = os.environ.get("SEEKDB_PASSWORD", "")
    SEEKDB_DATABASE = os.environ.get("ZHIYAN_DB", "zhiyan")
    COLLECTION = "zhiyan_corpus"

    # MiniMax — embeddings (embo-01) and grounded Q&A (MiniMax-M1)
    MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
    MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    MINIMAX_CHAT_MODEL = os.environ.get("MINIMAX_CHAT_MODEL", "MiniMax-M1")
    MINIMAX_EMBED_MODEL = "embo-01"
    EMBED_DIM = 1536

    # PowerContext — memory of weak knowledge points & review progress
    POWERCONTEXT_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000")
    POWERCONTEXT_SCOPE_TITLE = "智研学习记忆"

    # Server
    HOST = os.environ.get("ZHIYAN_HOST", "127.0.0.1")
    PORT = int(os.environ.get("ZHIYAN_PORT", "8010"))


settings = Settings()
