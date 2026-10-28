"""Embedding backends.

  local    deterministic character-n-gram hashing vector (stdlib only, default)
  minimax  MiniMax embo-01 via its OpenAI-compatible /v1/embeddings (needs a key)
  openai   any OpenAI-compatible /v1/embeddings endpoint

`local` exists so a reviewer can run the whole project without an API key — the
hybrid pipeline (vector leg + ik fulltext leg + RRF) is genuinely exercised either
way, because the fulltext leg is lexical by nature and the vector leg is whatever
backend you plug in. Set ZHIYAN_EMBED_BACKEND=minimax (or openai) for real
semantic quality; the records then report which model produced them.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request

LOCAL_DIM = 256


def _norm(vec):
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n if n else 0.0 for v in vec]


def _grams(text: str):
    low = re.sub(r"\s+", " ", text.lower())
    grams = re.findall(r"[a-z0-9]+", low)
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", low)
    for n in (1, 2, 3):
        grams.extend(cjk[i:i + n] for i in range(len(cjk) - n + 1))
    return grams


def local_embed(text: str, dim: int = LOCAL_DIM):
    tf = {}
    for g in _grams(text):
        tf[g] = tf.get(g, 0) + 1
    vec = [0.0] * dim
    for g, f in tf.items():
        h = int.from_bytes(hashlib.md5(g.encode("utf-8")).digest()[:8], "big")
        vec[h % dim] += (1.0 if (h >> 63) & 1 else -1.0) * (1.0 + math.log(f))
    return _norm(vec)


def _api_embed(texts, base_url, api_key, model, dim=None):
    out = []
    for start in range(0, len(texts), 16):
        batch = texts[start:start + 16]
        body = {"model": model, "input": batch}
        if dim:
            body["dimensions"] = dim
        req = urllib.request.Request(
            base_url.rstrip("/") + "/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
            raise RuntimeError(f"embedding 调用失败（{model}）：{exc}") from exc
        items = sorted(payload["data"], key=lambda d: d.get("index", 0))
        if len(items) != len(batch):
            raise RuntimeError(f"embedding 返回 {len(items)} 条，请求 {len(batch)} 条")
        out.extend([[float(v) for v in it["embedding"]] for it in items])
    return out


def minimax_embed(texts):
    key = os.getenv("MINIMAX_API_KEY", "")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY 未设置，无法使用 minimax 后端；"
                           "改用 ZHIYAN_EMBED_BACKEND=local 即可无密钥运行")
    from . import llm  # MiniMax's embeddings request shape lives in llm.py
    return llm.embed(texts)


def openai_embed(texts):
    base = os.getenv("EMBED_BASE_URL", "")
    key = os.getenv("EMBED_API_KEY", "")
    model = os.getenv("EMBED_MODEL", "")
    if not (base and model):
        raise RuntimeError("openai 后端需要 EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL")
    return _api_embed(texts, base, key, model)


def embed(texts, backend: str = "local"):
    if not texts:
        return []
    if backend == "local":
        return [local_embed(t) for t in texts]
    if backend == "minimax":
        return minimax_embed(texts)
    if backend == "openai":
        return openai_embed(texts)
    raise RuntimeError(f"未知 embedding 后端：{backend}")


def describe(backend: str) -> dict:
    if backend == "local":
        return {"backend": "local", "model": "char-ngram-hashing", "dim": LOCAL_DIM,
                "note": "离线确定性向量，无需密钥；语义泛化有限，可换 minimax/openai"}
    if backend == "minimax":
        return {"backend": "minimax", "model": os.getenv("MINIMAX_EMBED_MODEL", "embo-01"),
                "dim": None, "note": "真实语义嵌入，需要 MINIMAX_API_KEY"}
    return {"backend": backend, "model": os.getenv("EMBED_MODEL", ""), "dim": None,
            "note": "OpenAI 兼容 /v1/embeddings"}


def as_sql_vector(vec) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
