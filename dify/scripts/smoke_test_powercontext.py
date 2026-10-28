#!/usr/bin/env python3
"""PowerContext 记忆层自检:确认《读心酒馆》依赖的四个调用都真实可用。

用法:
    set PC_BASE_URL=http://127.0.0.1:8100
    set PC_TOKEN=<与 server 相同的 token>          # ACCESS_MODE=disabled 时留空
    python scripts/smoke_test_powercontext.py [玩家标识]

退出码 0 = 全部通过;非 0 = 有断言失败(打印具体响应)。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("PC_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
TOKEN = os.environ.get("PC_TOKEN", "").strip()
PROBE_QUERY = "读心酒馆 战绩 类别 偏好 回答 习惯 命中 问题数"

_failures: list[str] = []


def call(method: str, path: str, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method, headers=headers)
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


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)


def scope_payload(player: str) -> dict:
    """与 DSL 的 scope_body 节点完全一致:同一玩家每次得到同一个 scope。"""
    return {
        "title": f"mindtavern-{player}",
        "summary": "读心酒馆玩家记忆分区",
        "idempotency_key": f"mindtavern:v1:{player}",
        "external_references": [{"kind": "player", "value": player}],
    }


def main() -> int:
    player = sys.argv[1] if len(sys.argv) > 1 else "smoketest"
    print(f"PowerContext @ {BASE_URL}  玩家分区键 = {player}\n")

    st, health = call("GET", "/health/live")
    check("健康检查 /health/live", st == 200, f"http={st}")
    if st == 0:
        print("\n服务未启动或地址不可达。先运行 scripts/run_powercontext.ps1")
        return 1

    st1, s1 = call("POST", "/v1/scopes", scope_payload(player))
    st2, s2 = call("POST", "/v1/scopes", scope_payload(player))
    sid = s1.get("scope_id")
    check(f"幂等建区两次均成功 (http={st1}/{st2})", st1 in (200, 201) and st2 in (200, 201))
    check("同一玩家重复请求返回同一 scope_id", bool(sid) and s2.get("scope_id") == sid, str(sid))
    if not sid:
        print("\n拿不到 scope_id,后续断言无意义。", json.dumps(s1, ensure_ascii=False)[:400])
        return 1

    round_text = "读心酒馆战绩|第1局|类别=食物|答案=苹果|问题数=7|命中=是|观察:此客偏爱食物类。"
    st, body = call("POST", "/v1/memory/remember",
                    {"scope_id": sid, "kind": "round-record", "text": round_text,
                     "reason": "smoke test 植入"})
    check("写入记忆 /v1/memory/remember", st == 200 and body.get("changed") is True,
          f"http={st} changed={body.get('changed')}")

    st, body = call("POST", "/v1/context/prepare",
                    {"scope_id": sid, "query": PROBE_QUERY, "max_bytes": 4000})
    content = body.get("content") or ""
    check("召回记忆包 /v1/context/prepare", st == 200 and body.get("status") == "ready"
          and round_text[:12] in content, f"http={st} status={body.get('status')} bytes={body.get('content_bytes')}")

    st, body = call("POST", "/v1/memory/search",
                    {"scope_id": sid, "query": "苹果 食物", "limit": 5, "mode": "fts"})
    hits = body.get("hits") or []
    check("定向检索 /v1/memory/search", st == 200 and len(hits) >= 1,
          f"http={st} hits={len(hits)}")

    st, body = call("POST", "/v1/memory/entries/list", {"scope_id": sid, "limit": 20})
    entries = body.get("entries") or []
    check("审计可读 /v1/memory/entries/list", st == 200 and len(entries) >= 1,
          f"http={st} entries={len(entries)}")
    for e in entries[:5]:
        print(f"        · [{e.get('kind')}] {e.get('text')[:60]}")

    fresh = scope_payload(player + "-stranger")
    st, other = call("POST", "/v1/scopes", fresh)
    st, body = call("POST", "/v1/context/prepare",
                    {"scope_id": other.get("scope_id"), "query": PROBE_QUERY, "max_bytes": 4000})
    check("新玩家分区隔离(status=empty)", st == 200 and body.get("status") == "empty",
          f"http={st} status={body.get('status')}")

    print()
    if _failures:
        print("失败项:", ", ".join(_failures))
        return 1
    print("全部通过:《读心酒馆》所需的记忆读写链路可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
