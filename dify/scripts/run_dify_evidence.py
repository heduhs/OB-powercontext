#!/usr/bin/env python3
"""《读心酒馆》证据采集器:通过 Dify 应用 API 自动打完若干局,产出运行日志与"每局问题数"曲线。

不需要额外模型密钥:玩家由本脚本按"属性表"回答(确定性、可复现),
老周由 Dify 应用里的模型生成。每局都开一个新的 conversation,同一个 --user
→ 同一个 PowerContext 分区 → 直接验证"跨会话记忆"。

用法:
    set DIFY_API_KEY=app-xxxx
    set PC_BASE_URL=https://xxx.trycloudflare.com      # 可选,用于同时核对服务端读写
    set PC_TOKEN=<token>
    python scripts/run_dify_evidence.py --user laozhang --rounds 4 --out evidence/run-logs/dify-laozhang
    python scripts/run_dify_evidence.py --user xiaow --rounds 1 --out evidence/run-logs/dify-xiaow

产出:
    <out>.json   逐轮完整记录(输入/回复/会话状态/耗时/message_id)
    <out>.md     可读对局实录 + 每局问题数曲线
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("DIFY_API_BASE", "https://api.dify.ai/v1").rstrip("/")
API_KEY = os.environ.get("DIFY_API_KEY", "").strip()

# 玩家心里的候选词:属性表决定它对每个问题的真实回答
WORDS = {
    "苹果": dict(category="食物", living=True, plant=True, edible=True, fruit=True, sweet=True,
                 salty=False, cooked=False, animal=False, manmade=False, place=False, person=False,
                 fiction=False, electronic=False, in_home=True, outdoor=False, portable=True, red=True),
    "锅贴": dict(category="食物", living=False, plant=False, edible=True, fruit=False, sweet=False,
                 salty=True, cooked=True, animal=False, manmade=True, place=False, person=False,
                 fiction=False, electronic=False, in_home=True, outdoor=False, portable=True, red=False),
    "猫": dict(category="动物", living=True, plant=False, edible=False, fruit=False, sweet=False,
               salty=False, cooked=False, animal=True, manmade=False, place=False, person=False,
               fiction=False, electronic=False, in_home=True, outdoor=False, portable=False, red=False),
    "手机": dict(category="物品", living=False, plant=False, edible=False, fruit=False, sweet=False,
                 salty=False, cooked=False, animal=False, manmade=True, place=False, person=False,
                 fiction=False, electronic=True, in_home=True, outdoor=False, portable=True, red=False),
    "西湖": dict(category="地点", living=False, plant=False, edible=False, fruit=False, sweet=False,
                 salty=False, cooked=False, animal=False, manmade=False, place=True, person=False,
                 fiction=False, electronic=False, in_home=False, outdoor=True, portable=False, red=False),
    "米饭": dict(category="食物", living=False, plant=True, edible=True, fruit=False, sweet=False,
                 salty=False, cooked=True, animal=False, manmade=True, place=False, person=False,
                 fiction=False, electronic=False, in_home=True, outdoor=False, portable=True, red=False),
    "生菜": dict(category="食物", living=True, plant=True, edible=True, fruit=False, sweet=False,
                 salty=False, cooked=False, animal=False, manmade=False, place=False, person=False,
                 fiction=False, electronic=False, in_home=True, outdoor=False, portable=True, red=False),
    "李白": dict(category="人物", living=False, plant=False, edible=False, fruit=False, sweet=False,
                 salty=False, cooked=False, animal=False, manmade=False, place=False, person=True,
                 fiction=False, electronic=False, in_home=False, outdoor=False, portable=False, red=False),
    "龙": dict(category="虚构", living=True, plant=False, edible=False, fruit=False, sweet=False,
               salty=False, cooked=False, animal=False, manmade=False, place=False, person=False,
               fiction=True, electronic=False, in_home=False, outdoor=False, portable=False, red=True),
}

# 问题关键词 → 属性;按顺序取第一条命中
RULES = [
    (["水果"], "fruit"),
    (["动物", "四条腿", "有腿", "毛"], "animal"),
    (["植物", "长在地里", "树上长", "庄稼"], "plant"),
    (["人造", "人工", "制造", "做出来", "工厂", "产品"], "manmade"),
    (["电子", "屏幕", "插电", "充电", "数码", "app", "App"], "electronic"),
    (["地点", "地方", "城市", "景区", "风景", "旅游"], "place"),
    (["人物", "人名", "真人", "历史", "诗人", "有名的人"], "person"),
    (["虚构", "神话", "传说", "动画", "童话", "不存在", "故事里"], "fiction"),
    (["甜"], "sweet"),
    (["咸"], "salty"),
    (["煎", "炸", "烤", "煮熟", "做熟", "生的吃", "加热", "生吃"], "cooked"),
    (["吃", "食物", "饿", "嘴", "厨", "餐"], "edible"),
    (["有生命", "活着", "生命", "活的", "死物"], "living"),
    (["户外", "室外", "露在天上", "在野外"], "outdoor"),
    (["家里", "房间", "厨房", "屋里", "家中"], "in_home"),
    (["口袋", "随身", "带走", "拿在手里", "搬得动"], "portable"),
    (["红色", "红的", "颜色"], "red"),
]

VERDICT_WORDS = ["猜中了", "没猜中", "对了", "不对", "中了", "没中"]


def ask_api(query: str, conversation_id: str, user: str) -> dict:
    body = json.dumps({"inputs": {}, "query": query, "response_mode": "blocking",
                       "conversation_id": conversation_id, "user": user},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{API_BASE}/chat-messages", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {API_KEY}",
                                          "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            if exc.code in (400, 500) and attempt < 2:
                time.sleep(2)
                continue
            return {"_error": f"HTTP {exc.code}: {raw[:200]}"}
        except Exception as exc:  # noqa: BLE001
            if attempt < 2:
                time.sleep(2)
                continue
            return {"_error": repr(exc)}
    return {"_error": "unreachable"}


def player_answer(question: str, secret: dict) -> str:
    # 只取最后一个真正的疑问句:老周开场会复述"食物、动物、物品…"类别表,
    # 若拿整段去匹配关键词,会命中错的那一类,导致全程答错。
    lines = [ln.strip() for ln in question.splitlines() if ln.strip()]
    last_line = lines[-1] if lines else question
    segs = [x.strip() for x in re.split(r"[?？]", last_line) if x.strip()]
    tail = segs[-1] if segs else last_line
    for scope in (tail, question):
        for keys, attr in RULES:
            if any(k in scope for k in keys):
                val = secret.get(attr)
                if val is not None:
                    return "是" if val else "不是"
    return "不知道"


def is_bet(text: str, secret_word: str) -> bool:
    """判定老周这一句是不是在下注。

    开场白里也会出现"下注/猜"等词,所以要求出现明确的押注标记(我押/押一把/是不是 X),
    或者直接点了玩家心里那个词。
    """
    if any(w in text for w in VERDICT_WORDS):
        return False
    if secret_word in text:
        return True
    strong = ["我押", "押一把", "那就押", "压注", "下注了", "我猜是"]
    return any(s in text for s in strong)


def questions_count(reply: str) -> int | None:
    m = re.search(r"(\d+)\s*个?问题", reply)
    return int(m.group(1)) if m else None


def play_round(user: str, round_no: int, secret_word: str, max_turns: int, verbose: bool):
    secret = dict(WORDS[secret_word])
    conversation_id = ""
    transcript = []
    result = {"round": round_no, "secret": secret_word, "category": secret["category"],
              "questions": None, "hit": None, "turns": 0, "settled": False}
    say = "开局"
    for turn in range(max_turns):
        resp = ask_api(say, conversation_id, user)
        if "_error" in resp:
            transcript.append({"turn": turn, "player": say, "host": f"(调用失败 {resp['_error']})"})
            result["error"] = resp["_error"]
            break
        conversation_id = resp.get("conversation_id") or conversation_id
        reply = (resp.get("answer") or "").strip()
        result["turns"] = turn + 1
        transcript.append({"turn": turn, "player": say, "host": reply,
                           "message_id": resp.get("message_id"),
                           "elapsed": resp.get("elapsed_time"),
                           "total_tokens": (resp.get("metadata") or {}).get("total_tokens")})
        if verbose:
            print(f"  玩家: {say}")
            print(f"  老周: {reply[:160]}")
        qn = questions_count(reply)
        if qn is not None:
            result["questions"] = qn
        if "📔" in reply:                       # 结算回执 = 本局结束、记忆已写入
            result["settled"] = True
            result["hit"] = "猜中了" in say or ("中了" in say and "没猜中" not in say)
            break
        if is_bet(reply, secret_word):
            say = f"猜中了,我想的就是{secret_word}" if secret_word in reply else \
                  f"没猜中,其实我想的是{secret_word},换一局"
            continue
        say = player_answer(reply, secret)
    return result, transcript, conversation_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="laozhang")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--words", default="", help="逗号分隔,指定每局想什么;默认按 食物→食物→物品→地点 顺序")
    ap.add_argument("--out", default="evidence/run-logs/dify")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not API_KEY:
        print("请先设置 DIFY_API_KEY(应用 API 密钥)")
        return 1
    default_words = ["苹果", "锅贴", "米饭", "生菜", "猫", "手机", "西湖", "李白"]
    words = [w.strip() for w in args.words.split(",") if w.strip()] or default_words

    all_rounds = []
    print(f"玩家 user={args.user}  连打 {args.rounds} 局(每局一个新会话)")
    for i in range(args.rounds):
        word = words[i % len(words)]
        print(f"\n=== 第 {i + 1} 局(心里想:{word}) ===")
        res, tr, cid = play_round(args.user, i + 1, word, args.max_turns, not args.quiet)
        res["transcript"] = tr
        res["conversation_id"] = cid
        all_rounds.append(res)
        print(f"  —— 本局:{'命中' if res.get('hit') else '未中/未结算'},"
              f"老周记账问题数 = {res.get('questions')},实际轮次 = {res['turns']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"user": args.user, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "api_base": API_BASE, "rounds": all_rounds}
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# 读心酒馆 · 自动对局实录(玩家 `{args.user}`)\n",
             f"- 生成时间:{payload['generated_at']}",
             f"- 应用 API:{API_BASE}",
             "- 玩家由脚本按属性表回答(确定性、可复现);老周由 Dify 应用内的模型生成。\n",
             "| 局 | 心里想的 | 类别 | 老周记账的问题数 | 实际轮次 | 结果 | 会话 ID |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in all_rounds:
        lines.append(f"| {r['round']} | {r['secret']} | {r['category']} | {r.get('questions')} | "
                     f"{r['turns']} | {'命中' if r.get('hit') else '未中'} | `{str(r.get('conversation_id'))[:8]}` |")
    lines.append("\n## 逐轮实录\n")
    for r in all_rounds:
        lines.append(f"### 第 {r['round']} 局 · 想的是「{r['secret']}」\n")
        for t in r["transcript"]:
            lines.append(f"- 玩家:{t['player']}")
            lines.append(f"  - 老周:{t['host']}")
        lines.append("")
    out.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")

    qs = [r.get("questions") for r in all_rounds if r.get("questions")]
    print("\n=== 每局问题数 ===")
    for r in all_rounds:
        print(f"  第 {r['round']} 局:{r.get('questions')} 问  ({'命中' if r.get('hit') else '未中'})")
    if len(qs) >= 2:
        print(f"  首局 {qs[0]} 问 → 末局 {qs[-1]} 问")
    print(f"\n已写出 {out.with_suffix('.json')} 与 {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
