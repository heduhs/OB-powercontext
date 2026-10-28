#!/usr/bin/env python3
"""《读心酒馆》DSL 回放执行器。

它不做第二套实现:直接读 app/mindtavern_chatflow.yml,按图逐节点执行
(code 节点真的跑 DSL 里的 Python,http 节点真的打 PowerContext,llm 节点真的调模型,
 if-else / 聚合 / 赋值节点按 DSL 声明的语义走),因此:

  1) 导入 Dify 之前,先用它把流程、提示词、PowerContext 读写链路跑一遍;
  2) 没有 Dify 环境的人(评审)也能用它完整体验玩法,并拿到逐节点运行日志;
  3) --player-llm 让第二个模型扮演玩家,自动打完一局,用于产出"每局问题数"曲线。

用法:
    set PC_BASE_URL=http://127.0.0.1:8100
    set PC_TOKEN=<token>
    set LLM_API_KEY=<minimax key>

    # 手动对话(同一个 --user 换 --conversation 即为"跨会话")
    python tools/dsl_replay.py --user laozhang --conversation s1 --say 开局
    python tools/dsl_replay.py --user laozhang --conversation s1 --say 是

    # 自动打完 4 局,输出运行日志与曲线
    python tools/dsl_replay.py --autolive rounds=4 --user laozhang --out evidence/run-logs/autolive

常用参数:
    --dry            不调模型、不打 PowerContext,只跑图结构与代码节点
    --trace          打印每个节点的输入输出
    --max-turns N    自动模式下每局最多多少轮(默认 26)
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

import yaml

HERE = Path(__file__).resolve().parent
DSL_DEFAULT = HERE.parent / "app" / "mindtavern_chatflow.yml"

REF_RE = re.compile(r"\{\{#([A-Za-z0-9_]{1,50}(?:\.[A-Za-z_]\w{0,29}){1,10})#\}\}")
NULL_OPS = {"empty", "not empty", "null", "not null"}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class FlowError(RuntimeError):
    pass


class Store:
    """节点输出 + 会话变量 + 环境变量 + 系统变量的取值层。"""

    def __init__(self, dsl: dict, state: dict, user: str, conversation: str):
        wf = dsl["workflow"]
        self.env = {v["name"]: v.get("value") for v in wf.get("environment_variables") or []}
        for k, v in (("PC_BASE_URL", env("PC_BASE_URL")), ("PC_TOKEN", env("PC_TOKEN"))):
            if v:
                self.env[k] = v
        self.conv = state.setdefault("conversation_variables", {})
        for v in wf.get("conversation_variables") or []:
            self.conv.setdefault(v["name"], v.get("value"))
        self.sys = {
            "query": "",
            "user_id": user,
            "conversation_id": conversation,
            "dialog_mode": "advanced-chat",
            "files": [],
            "timestamp": int(time.time()),
        }
        self.nodes: dict[str, dict] = {}

    def get(self, head: str, field: str):
        if head == "sys":
            return self.sys.get(field)
        if head == "env":
            return self.env.get(field)
        if head == "conversation":
            return self.conv.get(field)
        return (self.nodes.get(head) or {}).get(field)

    def render(self, text):
        if not isinstance(text, str):
            return text

        def sub(m):
            head, _, field = m.group(1).partition(".")
            val = self.get(head, field)
            if val is None:
                return ""
            return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)

        return REF_RE.sub(sub, text)

    def has(self, head: str, field: str) -> bool:
        return self.get(head, field) is not None


class Engine:
    def __init__(self, dsl: dict, store: Store, dry: bool, trace: bool):
        self.dsl = dsl
        self.store = store
        self.dry = dry
        self.trace = trace
        self.nodes = {n["id"]: n for n in dsl["workflow"]["graph"]["nodes"]}
        self.adj: dict[str, list[tuple[str, str]]] = {}
        for e in dsl["workflow"]["graph"]["edges"]:
            self.adj.setdefault(e["source"], []).append((e.get("sourceHandle", "source"), e["target"]))
        self.records: list[dict] = []
        self.http_calls: list[dict] = []
        self.llm_calls: list[dict] = []

    def log(self, nid, ntype, title, payload):
        rec = {"node": nid, "type": ntype, "title": title, **payload}
        self.records.append(rec)
        if self.trace:
            print(f"    · {nid} [{ntype}] {json.dumps(payload, ensure_ascii=False)[:220]}")

    def next_nodes(self, nid, handle):
        return [t for h, t in self.adj.get(nid, []) if h == handle]

    def run(self, start_id: str) -> str:
        current = self.next_nodes(start_id, "source")
        answer = ""
        guard = 0
        while current:
            guard += 1
            if guard > 200:
                raise FlowError("节点执行超过 200 步,疑似成环")
            nid = current[0]
            node = self.nodes[nid]
            data = node["data"]
            t = data["type"]
            runner = getattr(self, f"run_{t.replace('-', '_')}", None)
            if runner is None:
                raise FlowError(f"回放器尚未实现 {t} 节点")
            handle, out = runner(nid, data)
            self.store.nodes[nid] = out
            self.log(nid, t, data.get("title", ""), out if t != "llm" else {"text_len": len(out.get("text") or "")})
            if t == "answer":
                answer = out.get("answer", "")
                break
            current = self.next_nodes(nid, handle)
        return answer

    # ---------------- 节点实现 ----------------
    def run_start(self, nid, data):
        return "source", {}

    def run_code(self, nid, data):
        inputs = {}
        for v in data.get("variables") or []:
            inputs[v["variable"]] = self.store.get(*v["value_selector"])
        if self.dry:
            return "source", {k: None for k in (data.get("outputs") or {})}
        ns: dict = {}
        exec(compile(data["code"], f"<{nid}>", "exec"), ns)
        try:
            out = ns["main"](**inputs)
        except TypeError as exc:
            raise FlowError(f"代码节点 {nid} 入参不匹配: {exc}") from exc
        declared = set((data.get("outputs") or {}).keys())
        if not isinstance(out, dict):
            raise FlowError(f"代码节点 {nid} 必须返回 dict")
        missing = declared - set(out)
        if missing:
            raise FlowError(f"代码节点 {nid} 未返回声明的输出 {sorted(missing)}")
        return "source", {k: out.get(k) for k in declared}

    def run_http_request(self, nid, data):
        url = self.store.render(data["url"])
        headers = {}
        for line in (self.store.render(data.get("headers") or "")).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip()] = v.strip()
        body = ""
        bdata = (data.get("body") or {}).get("data") or []
        if bdata:
            body = self.store.render(bdata[0].get("value") or "")
        method = (data.get("method") or "get").upper()
        rcfg = data.get("retry_config") or {}
        attempts = (rcfg.get("max_retries", 0) + 1) if rcfg.get("retry_enabled") else 1
        if self.dry:
            return "source", {"status_code": 0, "body": "", "headers": ""}
        timeout = (data.get("timeout") or {}).get("read", 30)
        last = None
        for i in range(max(1, attempts)):
            req = urllib.request.Request(url, data=body.encode("utf-8"), method=method, headers=headers)
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    code, raw = resp.status, resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                code, raw = exc.code, exc.read().decode("utf-8", "replace")
            except Exception as exc:  # 连接层失败:Dify 也会让节点失败
                last = exc
                time.sleep(0.4)
                continue
            self.http_calls.append({
                "node": nid, "method": method, "url": url, "request_body": body,
                "status": code, "response": raw[:4000], "elapsed_ms": int((time.time() - t0) * 1000),
                "attempt": i + 1,
            })
            return "source", {"status_code": code, "body": raw, "headers": ""}
        raise FlowError(f"HTTP 节点 {nid} 无法连通 {url}: {last!r}")

    def run_if_else(self, nid, data):
        for case in data.get("cases") or []:
            results = []
            for c in case.get("conditions") or []:
                head, field = c["variable_selector"]
                val = self.store.get(head, field)
                results.append(self.compare(val, c["comparison_operator"], c.get("value")))
            ok = all(results) if case.get("logical_operator", "and") == "and" else any(results)
            if ok:
                return case.get("case_id", "true"), {"result": case.get("case_id")}
        return "false", {"result": "false"}

    @staticmethod
    def compare(val, op, want, _extra=False):
        sval = "" if val is None else (val if isinstance(val, str) else str(val))
        swant = "" if want is None else str(want)
        if op == "is":
            return sval == swant
        if op == "is not":
            return sval != swant
        if op == "contains":
            return swant in sval
        if op == "not contains":
            return swant not in sval
        if op == "start with":
            return sval.startswith(swant)
        if op == "empty" or op == "null":
            return sval == ""
        if op in ("not empty", "not null"):
            return sval != ""
        if op in ("=", "≠", ">", "<", "≥", "≤"):
            try:
                a, b = float(val), float(want)
            except (TypeError, ValueError):
                return False
            return {"=": a == b, "≠": a != b, ">": a > b, "<": a < b, "≥": a >= b, "≤": a <= b}[op]
        raise FlowError(f"回放器不支持比较符 {op}")

    def run_variable_aggregator(self, nid, data):
        for sel in data.get("variables") or []:
            val = self.store.get(*sel)
            if val is not None:
                return "source", {"output": val}
        return "source", {"output": None}

    def run_assigner(self, nid, data):
        for it in data.get("items") or []:
            name = it["variable_selector"][-1]
            cur = self.store.conv.get(name)
            op = it.get("operation")
            if it.get("input_type") == "variable":
                val = self.store.get(*it["value"])
            else:
                val = it.get("value")
            if op == "over-write":
                self.store.conv[name] = val
            elif op in ("+= ", "+=", "-=", "*=", "/="):
                a, b = float(cur or 0), float(val or 0)
                self.store.conv[name] = {"+=": a + b, "-=": a - b, "*=": a * b, "/=": a / b}[op.strip()]
            elif op == "clear":
                self.store.conv[name] = ""
            elif op == "append":
                lst = cur if isinstance(cur, list) else []
                lst.append(val)
                self.store.conv[name] = lst
            else:
                raise FlowError(f"赋值节点 {nid} 不支持的操作 {op}")
        return "source", {}

    def run_llm(self, nid, data):
        if self.dry:
            return "source", {"text": "", "usage": {}}
        msgs = []
        for p in data.get("prompt_template") or []:
            if p.get("role") in ("system", "user", "assistant"):
                msgs.append({"role": p["role"], "content": self.store.render(p.get("text") or "")})
        win = ((data.get("memory") or {}).get("window")) or {}
        if win.get("enabled"):
            hist = self.store.nodes.get("__history__", {}).get("messages", [])[-int(win.get("size", 10)):]
            msgs = [{"role": "system", "content": "(以下是本次会话的历史,按时间顺序)"}, *hist,
                    *[m for m in msgs if m["role"] != "system"], ] if hist else msgs
        model = data.get("model") or {}
        name = env("LLM_MODEL", model.get("name") or "MiniMax-M2")
        base = env("LLM_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
        key = env("LLM_API_KEY")
        if not key:
            raise FlowError("缺少 LLM_API_KEY(OpenAI 兼容端点的密钥)")
        payload = {"model": name, "messages": msgs, "stream": False}
        payload.update({k: v for k, v in (model.get("completion_params") or {}).items()
                        if k in ("temperature", "top_p", "max_tokens")})
        t0 = time.time()
        req = urllib.request.Request(
            f"{base}/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data_out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise FlowError(f"模型调用失败 {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
        except Exception as exc:  # noqa: BLE001
            raise FlowError(f"模型调用失败: {exc!r}") from exc
        text = data_out["choices"][0]["message"]["content"]
        self.llm_calls.append({"node": nid, "model": name, "base": base,
                               "messages": msgs, "response": text,
                               "usage": data_out.get("usage"), "elapsed_ms": int((time.time() - t0) * 1000)})
        return "source", {"text": text, "usage": data_out.get("usage") or {}}

    def run_answer(self, nid, data):
        return "source", {"answer": self.store.render(data.get("answer") or "")}


def state_path(state_dir: Path, user: str, conversation: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{user}__{conversation}")
    return state_dir / f"{safe}.json"


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"conversation_variables": {}, "history": []}


def main() -> int:
    ap = argparse.ArgumentParser(description="按 DSL 逐节点回放《读心酒馆》")
    ap.add_argument("--dsl", default=str(DSL_DEFAULT))
    ap.add_argument("--user", default="laozhang", help="玩家标识:同一个 user 换 conversation 即跨会话")
    ap.add_argument("--conversation", default="s1")
    ap.add_argument("--say", action="append", default=[], help="玩家说的话,可重复")
    ap.add_argument("--state-dir", default=str(HERE.parent / ".replay-state"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--autolive", default="", help="自动对局,例如 rounds=4")
    ap.add_argument("--max-turns", type=int, default=26)
    ap.add_argument("--out", default="", help="运行日志输出目录")
    args = ap.parse_args()

    dsl = yaml.safe_load(Path(args.dsl).read_text(encoding="utf-8"))
    start_id = next(n["id"] for n in dsl["workflow"]["graph"]["nodes"] if n["data"]["type"] == "start")
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    if args.autolive and not args.say:
        return autolive(args, dsl, start_id, state_dir)

    if not args.say:
        args.say = ["开局"]
    turns = []
    for say in args.say:
        turns.append(play_turn(dsl, start_id, state_dir, args, say))
    for t in turns:
        print(f"\n玩家: {t['say']}\n老周: {t['reply']}")
    return 0


def play_turn(dsl, start_id, state_dir, args, say: str) -> dict:
    path = state_path(state_dir, args.user, args.conversation)
    state = load_state(path)
    store = Store(dsl, state, args.user, args.conversation)
    store.sys["query"] = say
    store.nodes["__history__"] = {"messages": state.get("history", [])}
    engine = Engine(dsl, store, args.dry, args.trace)
    t0 = time.time()
    reply = engine.run(start_id)
    hist = state.setdefault("history", [])
    hist.append({"role": "user", "content": say})
    hist.append({"role": "assistant", "content": store.nodes.get("host_llm", {}).get("text") or reply})
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "say": say, "reply": reply, "elapsed_ms": int((time.time() - t0) * 1000),
        "conversation_variables": dict(store.conv), "nodes": engine.records,
        "powercontext_calls": engine.http_calls, "llm_calls": engine.llm_calls,
    }


PLAYER_SYSTEM = """你在陪酒馆老板玩"心里想一个东西让他猜"的游戏。你要做三件事:
1) 第一轮:先决定你心里想的具体名词(六类之一:食物/动物/物品/地点/人物/虚构),之后绝不改变。想的时候尽量"有你的习惯"——比如你确实偏爱某一类,连着几局都往同一类想。
2) 对老板的是/否问题给出真实回答,只能从「是」「不是」「看情况」「不知道」「跳过」里选一个,单独成句。
3) 如果老板这句话是在下注/给候选/直接猜答案,你要判定:
   - 猜中了 → 回:"猜中了,我想的是〈那个词〉"
   - 没猜中 → 回:"没猜中,其实我想的是〈那个词〉,换一局"
只输出你要对老板说的那一句话,不要解释、不要加引号。
另外你还要在心里记住你的身份:你是一个连玩多局的常客,说话随意,偶尔自称时用一个固定的称呼。
"""


def ask_player(client, secret_hint: str, host_line: str) -> str:
    if client is None:
        return "是"
    payload = {"model": env("LLM_MODEL", "MiniMax-M2"), "stream": False, "temperature": 0.9,
               "messages": [{"role": "system", "content": PLAYER_SYSTEM + secret_hint},
                            {"role": "user", "content": f"老板说:{host_line}\n请给出你这一轮的回应。"}]}
    req = urllib.request.Request(
        env("LLM_BASE_URL", "https://api.minimax.io/v1").rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {env('LLM_API_KEY')}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"].strip()


def pick_secret(client) -> str:
    payload = {"model": env("LLM_MODEL", "MiniMax-M2"), "stream": False, "temperature": 1.0,
               "messages": [{"role": "user", "content":
                             "从「食物/动物/物品/地点/人物/虚构」里先选一类,再从这一类里选一个常见具体名词。"
                             "只输出:类别|名词"}]}
    req = urllib.request.Request(
        env("LLM_BASE_URL", "https://api.minimax.io/v1").rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {env('LLM_API_KEY')}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    text = out["choices"][0]["message"]["content"].strip().replace("｜", "|")
    if "|" in text:
        cat, _, word = text.partition("|")
        return cat.strip()[:8] + "|" + word.strip()[:20]
    return "未知|" + text[:20]


def autolive(args, dsl, start_id, state_dir) -> int:
    rounds = 1
    m = re.search(r"rounds=(\d+)", args.autolive)
    if m:
        rounds = int(m.group(1))
    client = None if args.dry else object()
    transcript, curve, all_turns = [], [], []
    for r in range(1, rounds + 1):
        conversation = f"auto-r{r}"
        print(f"\n=== 第 {r} 局(会话 {conversation},玩家 {args.user})===")
        questions = 0
        settled = False
        for turn in range(args.max_turns):
            if turn == 0:
                say = "开局"
                if client is not None:
                    pass
            else:
                host = transcript[-1]["reply"] if transcript else ""
                if client is None:
                    say = "是"
                else:
                    say = ask_player(client, "", host)
            rec = play_turn(dsl, start_id, state_dir, args, say)
            reply = rec["reply"]
            all_turns.append({**{k: rec[k] for k in ("say", "reply", "conversation_variables")},
                              "round": r, "conversation": conversation,
                              "powercontext_calls": rec["powercontext_calls"],
                              "llm_calls": [{"model": c["model"], "response": c["response"]}
                                            for c in rec["llm_calls"]],
                              "nodes": rec["nodes"]})
            transcript.append({"round": r, "say": say, "reply": reply})
            print(f"  玩家: {say}\n  老周: {reply}")
            state = json.loads(state_path(state_dir, args.user, conversation).read_text(encoding="utf-8"))
            if say.strip()[:2] in ("猜中", "没猜", "其实", "换一") or "📔" in reply:
                settled = True
            if "📔" in reply:
                questions = int(state["conversation_variables"].get("asked_count") or 0)
                break
        curve.append({"round": r, "conversation": conversation, "settled": settled,
                      "asked_count_at_settle": questions})
        print(f"  —— 本局结束:问题数 {questions}")
    print("\n=== 每局问题数 ===")
    for c in curve:
        print(f"  第 {c['round']} 局: {c['asked_count_at_settle']} 问")
    if args.out:
        outdir = Path(args.out)
        outdir.parent.mkdir(parents=True, exist_ok=True)
        payload = {"user": args.user, "curve": curve, "turns": all_turns}
        Path(str(outdir) + ".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"# 自动对局运行日志(玩家 {args.user})\n"]
        for t in all_turns:
            lines.append(f"\n[第{t['round']}局 · {t['conversation']}] 玩家: {t['say']}")
            lines.append(f"老周: {t['reply']}")
            lines.append(f"会话变量: {json.dumps(t['conversation_variables'], ensure_ascii=False)}")
            for call in t["powercontext_calls"]:
                lines.append(f"  → PowerContext {call['method']} {call['url']} "
                             f"[{call['status']}] {call['elapsed_ms']}ms")
                lines.append(f"    请求: {call['request_body'][:220]}")
                lines.append(f"    应答: {call['response'][:220]}")
        lines.append("\n## 每局问题数\n")
        for c in curve:
            lines.append(f"- 第 {c['round']} 局:{c['asked_count_at_settle']} 问")
        Path(str(outdir) + ".md").write_text("\n".join(lines), encoding="utf-8")
        print(f"\n运行日志已写出: {str(outdir)}.json / {str(outdir)}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
