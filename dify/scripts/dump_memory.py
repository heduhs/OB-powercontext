#!/usr/bin/env python3
"""把某个玩家在 PowerContext 里的记忆全查出来,作为"记忆真的被写入并可审计"的证据。

用法:
    set PC_BASE_URL=http://127.0.0.1:8100
    set PC_TOKEN=<token>
    python scripts/dump_memory.py --user laozhang
    python scripts/dump_memory.py --user laozhang --format md --out evidence/memory-laozhang.md

同一个 --user 会命中同一个 scope(与 DSL 的建区逻辑一致:幂等键 mindtavern:v1:<user>)。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PC_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
TOKEN = os.environ.get("PC_TOKEN", "").strip()
PROBE = "读心酒馆 战绩 类别 偏好 回答 习惯 命中 问题数 观察 玩家"


def call(method: str, path: str, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": repr(exc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="玩家标识,与 Dify 里 sys.user_id 对应的键")
    ap.add_argument("--format", choices=["text", "md", "json"], default="text")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    st, scope = call("POST", "/v1/scopes", {
        "title": f"mindtavern-{args.user}",
        "summary": "读心酒馆玩家记忆分区",
        "idempotency_key": f"mindtavern:v1:{args.user}",
        "external_references": [{"kind": "player", "value": args.user}],
    })
    if st not in (200, 201) or not scope.get("scope_id"):
        print(f"取 scope 失败 http={st}: {json.dumps(scope, ensure_ascii=False)[:300]}")
        return 1
    sid = scope["scope_id"]

    st, listing = call("POST", "/v1/memory/entries/list", {"scope_id": sid, "limit": 100})
    entries = listing.get("entries") or []
    st, prepared = call("POST", "/v1/context/prepare",
                        {"scope_id": sid, "query": PROBE, "max_bytes": 4000})

    if args.format == "json":
        text = json.dumps({"scope_id": sid, "entries": entries,
                           "prepared": prepared}, ensure_ascii=False, indent=2)
    elif args.format == "md":
        lines = [f"# 玩家 `{args.user}` 在 PowerContext 里的记忆\n",
                 f"- scope_id: `{sid}`", f"- 记忆条数: {len(entries)}",
                 f"- 召回状态: {prepared.get('status')}({prepared.get('content_bytes')} 字节)\n",
                 "| # | kind | 状态 | 记忆内容 |", "| --- | --- | --- | --- |"]
        for i, e in enumerate(entries, 1):
            lines.append(f"| {i} | `{e.get('kind')}` | {e.get('state')} | {str(e.get('text')).replace('|', chr(92) + '|')} |")
        lines.append("\n## 注入给模型的上下文包(老周的账本)\n\n```text\n"
                     + (prepared.get("content") or "(空)") + "\n```")
        text = "\n".join(lines)
    else:
        lines = [f"scope_id = {sid}", f"记忆条数 = {len(entries)}",
                 f"召回状态 = {prepared.get('status')} ({prepared.get('content_bytes')} 字节)", ""]
        for i, e in enumerate(entries, 1):
            lines.append(f"  {i:>2}. [{e.get('kind')}] {e.get('text')}")
        lines += ["", "—— 注入给模型的上下文包 ——", (prepared.get("content") or "(空)")[:2000]]
        text = "\n".join(lines)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"已写出 {args.out}({len(entries)} 条记忆)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
