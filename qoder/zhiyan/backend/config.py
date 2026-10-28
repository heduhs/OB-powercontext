"""ZhiYan configuration — everything comes from environment variables.

No credential is ever committed. The defaults are chosen so that a reviewer can
run the whole project with **no API key at all**: seekdb and PowerContext run
locally, and the default embedding backend is offline.
"""

from __future__ import annotations

import os


class Settings:
    # ---- seekdb (MySQL protocol) — storage + hybrid retrieval ----
    SEEKDB_HOST = os.environ.get("SEEKDB_HOST", "127.0.0.1")
    SEEKDB_PORT = int(os.environ.get("SEEKDB_PORT", "2881"))
    SEEKDB_USER = os.environ.get("SEEKDB_USER", "root")
    SEEKDB_PASSWORD = os.environ.get("SEEKDB_PASSWORD", "")
    SEEKDB_DATABASE = os.environ.get("ZHIYAN_DB", "zhiyan")

    # local | minimax | openai  (local needs no key and is the default)
    EMBED_BACKEND = os.environ.get("ZHIYAN_EMBED_BACKEND", "local")

    # ---- MiniMax (optional: semantic embeddings + grounded answers) ----
    MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
    MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    MINIMAX_CHAT_MODEL = os.environ.get("MINIMAX_CHAT_MODEL", "MiniMax-M1")
    MINIMAX_EMBED_MODEL = os.environ.get("MINIMAX_EMBED_MODEL", "embo-01")

    # ---- PowerContext — learning memory (weak points & review progress) ----
    POWERCONTEXT_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000")
    POWERCONTEXT_TOKEN = os.environ.get("POWERCONTEXT_TOKEN", "")
    POWERCONTEXT_SCOPE_TITLE = os.environ.get("ZHIYAN_SCOPE", "智研学习记忆")

    # ---- server ----
    HOST = os.environ.get("ZHIYAN_HOST", "127.0.0.1")
    PORT = int(os.environ.get("ZHIYAN_PORT", "8010"))


settings = Settings()
