"""Embedding backends used by build_index.py and evaluate.py.

  local   deterministic character-n-gram hashing embedding (stdlib only)
  api     any OpenAI-compatible POST /v1/embeddings endpoint
  seekdb  the in-database AI function  ai_embed(<model_alias>, <text>)

`local` is a smoke-test backend: it keeps the pipeline runnable with no network
and no model, and it is enough to compare a lexical vector against fulltext.
It is not a semantic model — never publish its recall/MRR as a quality claim.
Use `api` or `seekdb` for numbers you intend to act on.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DIM_DEFAULT = 256


def _norm(vec):
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n if n else 0.0 for v in vec]


def _ngrams(text: str):
    low = re.sub(r"\s+", " ", text.lower())
    grams = list(re.findall(r"[a-z0-9]+", low))
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", low)
    for n in (1, 2, 3):
        grams.extend(cjk[i:i + n] for i in range(len(cjk) - n + 1))
    return grams


def local_embed(text: str, dim: int = DIM_DEFAULT):
    """Signed hashing over character n-grams with sublinear term frequency."""
    tf = {}
    for gram in _ngrams(text):
        tf[gram] = tf.get(gram, 0) + 1
    vec = [0.0] * dim
    for gram, freq in tf.items():
        h = int.from_bytes(hashlib.md5(gram.encode("utf-8")).digest()[:8], "big")
        idx = h % dim
        vec[idx] += (1.0 if (h >> 63) & 1 else -1.0) * (1.0 + math.log(freq))
    return _norm(vec)


def _cache_path() -> Path:
    return Path(os.getenv("EMBED_CACHE",
                          str(Path(os.getenv("HOME", str(Path.cwd()))) / "embed_cache.json")))


def _load_cache() -> dict:
    path = _cache_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


def _key(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}|{text}".encode("utf-8")).hexdigest()


def _post_embeddings(base: str, key: str, body: dict) -> list:
    req = urllib.request.Request(
        base.rstrip("/") + "/embeddings",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"} if key else
                 {"Content-Type": "application/json"},
        method="POST",
    )
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            items = sorted(payload["data"], key=lambda d: d.get("index", 0))
            return [[float(v) for v in item["embedding"]] for item in items]
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, TimeoutError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"embedding API 调用失败：{last}")


def api_embed(texts, model: str, batch_size: int = 16):
    base = os.getenv("EMBED_BASE_URL", "").rstrip("/")
    key = os.getenv("EMBED_API_KEY", "")
    if not base:
        raise SystemExit(
            "backend=api 需要 EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL，例如\n"
            "  export EMBED_BASE_URL=https://api-inference.modelscope.cn/v1\n"
            "  export EMBED_API_KEY=ms-xxxxxxxx\n"
            "  export EMBED_MODEL=<embedding 模型名>"
        )
    if not model:
        raise SystemExit("backend=api 需要 EMBED_MODEL")
    cache = _load_cache()
    todo, seen = [], set()
    for text in texts:
        ck = _key(model, text)
        if ck not in cache and text not in seen:
            seen.add(text)
            todo.append((ck, text))
    send_dim = os.getenv("EMBED_SEND_DIMENSIONS") == "1"
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        body = {"model": model, "input": [t for _, t in chunk]}
        if send_dim:
            body["dimensions"] = int(os.getenv("EMBED_DIM", DIM_DEFAULT))
        vectors = _post_embeddings(base, key, body)
        if len(vectors) != len(chunk):
            raise SystemExit(f"embedding 返回 {len(vectors)} 条，请求 {len(chunk)} 条")
        for (ck, _), vec in zip(chunk, vectors):
            cache[ck] = vec
        _save_cache(cache)
        print(f"  embedded {min(start + batch_size, len(todo))}/{len(todo)}")
    return [cache[_key(model, t)] for t in texts]


def seekdb_embed(texts, cur, model_alias: str, dim: int | None = None):
    """Embed in-database with the AI function verified on seekdb 1.3.0.0:

        select ai_embed('<registered model name>', '<text>')
        select ai_embed('<registered model name>', '<text>', <dimension:int>)

    The model has to exist in the instance first, otherwise the server answers
    `ai model not found, please check if the model exists`. Note the third
    argument must be an integer literal: a float raises
    `dimension parameter must be an integer, not a decimal or float`.
    """
    if not model_alias:
        raise SystemExit("backend=seekdb 需要 --model <已注册的 AI 模型名>（或 SEEKDB_EMBED_MODEL）")
    out = []
    for text in texts:
        if dim:
            cur.execute("select ai_embed(%s, %s, %s)", (model_alias, text, int(dim)))
        else:
            cur.execute("select ai_embed(%s, %s)", (model_alias, text))
        row = cur.fetchone()[0]
        out.append(json.loads(row) if isinstance(row, str) else [float(v) for v in row])
    return out


def embed(texts, backend: str = "local", dim: int = DIM_DEFAULT,
          model: str | None = None, cur=None):
    if not texts:
        return []
    if backend == "local":
        return [local_embed(t, dim) for t in texts]
    if backend == "api":
        return api_embed(texts, model or os.getenv("EMBED_MODEL", ""))
    if backend == "seekdb":
        if cur is None:
            raise SystemExit("backend=seekdb 需要数据库 cursor")
        want_dim = None if os.getenv("SEEKDB_EMBED_NO_DIM") == "1" else dim
        return seekdb_embed(texts, cur, model or os.getenv("SEEKDB_EMBED_MODEL", ""), want_dim)
    raise SystemExit(f"未知 backend：{backend}")


def as_sql_vector(vec) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
