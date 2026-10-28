#!/usr/bin/env python3
"""《读心酒馆》DSL 结构校验器。

导入 Dify 之前先跑这个:它检查节点/连线/变量引用/分支句柄/可达性,
把"导进去才发现某个 {{#node.field#}} 打错了"这类问题挡在前面。

用法:
    python tools/validate_dsl.py [app/mindtavern_chatflow.yml]
退出码 0 = 通过(允许有 WARN),1 = 有 ERROR。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SYS_VARS = {"query", "files", "conversation_id", "user_id", "dialog_mode", "workflow_id",
            "workflow_name", "timestamp", "approved_at"}
NODE_OUTPUTS = {
    "http-request": {"status_code", "body", "headers", "files"},
    "llm": {"text", "usage", "files", "reasoning_content"},
    "variable-aggregator": {"output"},
    "template-transform": {"output"},
    "if-else": {"result"},
    "question-classifier": {"category", "reasoning_content"},
    "answer": set(),
    "assigner": set(),
    "start": set(),
}
REF = re.compile(r"\{\{#([A-Za-z0-9_]{1,50}(?:\.[A-Za-z_]\w{0,29}){1,10})#\}\}")
NODE_ID_OK = re.compile(r"^[A-Za-z0-9_]{1,50}$")

errors: list[str] = []
warns: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warns.append(msg)


def walk_strings(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("app/mindtavern_chatflow.yml")
    if not target.exists():
        print(f"找不到 DSL 文件: {target}")
        return 1
    doc = yaml.safe_load(target.read_text(encoding="utf-8"))

    for key in ("app", "kind", "version", "workflow"):
        if key not in doc:
            err(f"顶层缺少 {key}")
    if doc.get("kind") != "app":
        err(f"kind 必须是 app,当前 {doc.get('kind')!r}")
    version = doc.get("version")
    if not isinstance(version, str):
        err(f"version 必须是字符串(写成浮点数会导致导入失败),当前 {version!r}")
    mode = (doc.get("app") or {}).get("mode")
    if mode != "advanced-chat":
        err(f"本 DSL 应为 Chatflow(mode: advanced-chat),当前 {mode!r}")

    wf = doc.get("workflow") or {}
    graph = (wf.get("graph") or {})
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    ids = [n.get("id") for n in nodes]
    if len(ids) != len(set(ids)):
        err("节点 id 有重复")
    for n in nodes:
        nid = n.get("id", "")
        if not NODE_ID_OK.match(str(nid)):
            err(f"节点 id {nid!r} 不能用于 {{{{#...#}}}} 引用(只允许字母数字下划线)")
        if "positionAbsolute" not in n or "width" not in n or "height" not in n:
            warn(f"节点 {nid} 缺少 positionAbsolute/width/height,导入后画布会错位")

    by_id = {n["id"]: n for n in nodes if "id" in n}
    node_type = {nid: n["data"].get("type") for nid, n in by_id.items()}

    starts = [nid for nid, t in node_type.items() if t == "start"]
    answers = [nid for nid, t in node_type.items() if t == "answer"]
    if len(starts) != 1:
        err(f"必须恰好一个 start 节点,当前 {len(starts)}")
    if not answers:
        err("Chatflow 至少需要一个 answer 节点")
    bad = [nid for nid, t in node_type.items() if t in ("end", "trigger")]
    if bad:
        err(f"Chatflow 不允许 {bad}(那是 Workflow 的节点)")

    # 会话变量 / 环境变量声明
    SEGMENT_TYPES = {"string", "number", "integer", "float", "boolean", "object", "secret",
                     "array[string]", "array[number]", "array[integer]", "array[object]",
                     "array[boolean]", "file", "file-list"}
    conv = {v["name"] for v in (wf.get("conversation_variables") or []) if "name" in v}
    for v in wf.get("conversation_variables") or []:
        if "value" not in v or v.get("value") is None:
            err(f"会话变量 {v.get('name')} 缺少 value(Dify 导入会直接报错)")
        vt = v.get("value_type")
        if vt not in SEGMENT_TYPES:
            err(f"会话变量 {v.get('name')} 的 value_type={vt!r} 不是 Dify 认识的类型")
        if vt == "number":
            err("会话变量用了 value_type: number —— Dify 导入时不会规范化,运行时会在初始化阶段"
                "直接崩掉(表现为整条流程 500、一个节点都不执行)。数字请写 integer。")
        if "selector" not in v:
            err(f"会话变量 {v.get('name')} 缺少 selector")
    envs = {v["name"] for v in (wf.get("environment_variables") or []) if "name" in v}
    for v in wf.get("environment_variables") or []:
        if v.get("value_type") not in SEGMENT_TYPES:
            err(f"环境变量 {v.get('name')} 的 value_type={v.get('value_type')!r} 非法")

    # 连线
    out_handles: dict[str, set[str]] = {}
    for nid, n in by_id.items():
        t = n["data"].get("type")
        if t == "if-else":
            cases = {c.get("case_id") for c in n["data"].get("cases") or []}
            out_handles[nid] = cases | {"false"}
        else:
            out_handles[nid] = {"source"}
    seen_edge_ids = set()
    reach: set[str] = set()
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        eid = e.get("id")
        if eid in seen_edge_ids:
            err(f"连线 id 重复: {eid}")
        seen_edge_ids.add(eid)
        s, d = e.get("source"), e.get("target")
        if s not in by_id:
            err(f"连线 {eid} 的 source {s!r} 不存在")
        if d not in by_id:
            err(f"连线 {eid} 的 target {d!r} 不存在")
        sh = e.get("sourceHandle", "source")
        if s in by_id and sh not in out_handles[s]:
            err(f"连线 {eid} 用了句柄 {sh!r},但节点 {s} 没有这个出口(应为 {sorted(out_handles[s])})")
        adjacency.setdefault(s, []).append(d)

    if starts:
        stack, seen = list(adjacency.get(starts[0], [])), {starts[0]}
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adjacency.get(cur, []))
        reach = seen
        for nid in by_id:
            if nid not in reach:
                err(f"节点 {nid} 从 start 不可达(孤儿节点)")
        for nid in by_id:
            if node_type[nid] != "answer" and not adjacency.get(nid):
                err(f"节点 {nid} 没有出边,流程会走到死胡同")

    # 变量引用
    def resolve(head: str, field: str, ctx: str) -> None:
        if head == "sys":
            if field not in SYS_VARS:
                err(f"{ctx}: sys.{field} 不是已知系统变量")
        elif head == "env":
            if field not in envs:
                err(f"{ctx}: 引用了未声明的环境变量 env.{field}")
        elif head == "conversation":
            if field not in conv:
                err(f"{ctx}: 引用了未声明的会话变量 conversation.{field}")
        elif head in by_id:
            t = node_type[head]
            if head == starts[0] if starts else False:
                allowed = {v.get("variable") for v in by_id[head]["data"].get("variables") or []}
                if field not in allowed:
                    err(f"{ctx}: start 节点没有输入变量 {field}")
            elif t == "code":
                allowed = set((by_id[head]["data"].get("outputs") or {}).keys())
                if field not in allowed:
                    err(f"{ctx}: 代码节点 {head} 未声明输出 {field}(已声明 {sorted(allowed)})")
            elif t in NODE_OUTPUTS:
                if field not in NODE_OUTPUTS[t]:
                    err(f"{ctx}: {t} 节点 {head} 没有输出字段 {field}")
            else:
                warn(f"{ctx}: 引用了 {t} 节点 {head} 的 {field},校验器不认识该节点类型的输出")
        else:
            err(f"{ctx}: 引用了不存在的节点/前缀 {head}")

    for path, text in walk_strings(graph):
        for ref in REF.findall(text):
            parts = ref.split(".")
            if len(parts) != 2:
                warn(f"{path}: 引用 {ref} 层级不为两段,请人工确认")
                continue
            resolve(parts[0], parts[1], f"引用{path}")

    for n in nodes:
        d = n["data"]
        if d.get("type") == "code":
            for v in d.get("variables") or []:
                sel = v.get("value_selector") or []
                if len(sel) == 2:
                    resolve(sel[0], sel[1], f"代码节点 {n['id']} 输入 {v.get('variable')}")
                else:
                    err(f"代码节点 {n['id']} 的 value_selector 形状不对: {sel}")
            code = d.get("code") or ""
            try:
                compile(code, n["id"], "exec")
            except SyntaxError as exc:
                err(f"代码节点 {n['id']} 语法错误: {exc}")
            if "def main(" not in code:
                err(f"代码节点 {n['id']} 必须定义 main()")
        if d.get("type") == "http-request":
            for key in ("method", "url", "headers", "params", "authorization"):
                if key not in d:
                    err(f"HTTP 节点 {n['id']} 缺少必填字段 {key}")
        if d.get("type") == "assigner":
            if str(d.get("version")) != "2":
                err(f"赋值节点 {n['id']} 需要 version: '2'")
            for it in d.get("items") or []:
                sel = it.get("variable_selector") or []
                if sel[:1] != ["conversation"]:
                    err(f"赋值节点 {n['id']} 的写入目标必须是会话变量,当前 {sel}")
                if sel[-1] not in conv:
                    err(f"赋值节点 {n['id']} 写入了未声明的会话变量 {sel}")
                if it.get("input_type") == "variable":
                    v = it.get("value") or []
                    if len(v) == 2:
                        resolve(v[0], v[1], f"赋值节点 {n['id']}")
        if d.get("type") == "variable-aggregator":
            for sel in d.get("variables") or []:
                if len(sel) == 2:
                    resolve(sel[0], sel[1], f"聚合节点 {n['id']}")

    print(f"节点 {len(nodes)} · 连线 {len(edges)} · 会话变量 {len(conv)} · 环境变量 {len(envs)}")
    for w in warns:
        print("  WARN ", w)
    for e in errors:
        print("  ERROR", e)
    if errors:
        print(f"\n{len(errors)} 个错误,先修再导入 Dify。")
        return 1
    print("\n结构校验通过。下一步:在 Dify 里导入该 DSL,并按 docs/02-运行说明.md 配置模型与 PC_BASE_URL。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
