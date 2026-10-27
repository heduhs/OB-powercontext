"""MiniMax-driven code modification.

Builds a development prompt from the retrieved SDD context, calls the MiniMax
chat-completions API (OpenAI-compatible), applies the returned file rewrites
to the demo project, runs the target and regression tests, and records the
whole interaction as the MiniMax development record.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import settings

SYSTEM_PROMPT = """\
你是一名资深Python工程师，正在按规格驱动开发（SDD）流程完成一个开发任务。
你会收到：任务描述、适用的规格条目、需要修改的文件的完整源码、必须通过的测试。
请严格依据规格修改代码，保持所有既有行为（Invariants）不被破坏。

硬性约束：
- 只能修改“需要修改的代码”中已给出的文件；不得新建文件、不得新建模块。
- 不得引入任何当前文件中不存在的 import；所需的类和函数都已在给定源码中定义，直接复用。
- 保留每个文件原有的全部 import、类定义、函数与常量，只做实现目标功能所必需的改动。
- 输出的每个文件必须是完整、可直接运行、通过全部给定测试的源码。

输出要求：只输出一个JSON对象，不要输出其它文字，格式为：
{"files": {"<相对路径>": "<修改后的完整文件内容>"}, "summary": "<一句话说明改动>"}
只包含需要修改的文件；文件内容必须是完整可运行的Python源码。
"""


@dataclass
class EditResult:
    ok: bool
    summary: str = ""
    changed_files: List[str] = field(default_factory=list)
    target_tests_passed: Optional[bool] = None
    regression_tests_passed: Optional[bool] = None
    test_log: str = ""
    raw_response: str = ""
    error: str = ""


def _call_minimax(messages: List[dict]) -> str:
    url = settings.minimax_base_url.rstrip("/") + "/text/chatcompletion_v2"
    payload = json.dumps(
        {
            "model": settings.minimax_model,
            "messages": messages,
            "temperature": 0.2,
            # MiniMax-M1 is a reasoning model: reasoning_content consumes part
            # of the budget before the final answer, so allow ample tokens.
            "max_tokens": 32768,
        }
    ).encode()
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {settings.minimax_api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read().decode())
            try:
                return body["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as exc:
                raise RuntimeError(f"unexpected MiniMax response: {body}") from exc
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"MiniMax API call failed after retries: {last_exc}")


def build_prompt(task: str, context: dict, project_root: Optional[Path] = None) -> List[dict]:
    root = project_root or settings.project_root
    specs = "\n".join(
        f"- [{h['symbol']}] ({h['source']}) {h['text']}" for h in context["specs"]
    )
    # Provide the FULL current content of every file referenced by the code
    # hits, so the model edits real code and never invents modules/imports.
    seen_files: List[str] = []
    for h in context["code"]:
        if h["source"] not in seen_files:
            seen_files.append(h["source"])
    code_blocks = []
    for rel in seen_files:
        path = root / rel
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            content = next(
                (h["text"] for h in context["code"] if h["source"] == rel), ""
            )
        code_blocks.append(f"### 文件 {rel}（完整源码，需修改时输出整份文件）\n```python\n{content}\n```")
    code = "\n\n".join(code_blocks)
    tests = "\n\n".join(
        f"### 测试 {h['source']}::{h['symbol']}\n```python\n{h['text']}\n```"
        for h in context["tests"]
    )
    state = context.get("state") or []
    state_text = (
        "\n".join(f"- {json.dumps(s, ensure_ascii=False)}" for s in state)
        if state
        else "（无历史状态）"
    )
    user_prompt = f"""\
## 任务描述
{task}

## 适用规格条目
{specs}

## 需要修改的代码（相关文件完整源码）
{code}

## 必须通过的测试（修改前失败，修改后必须全部通过）
{tests}

## 已完成事项 / 既有验证结果（来自PowerContext保存的任务状态）
{state_text}

请按系统提示的JSON格式输出修改后的完整文件。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model output contains no JSON object")
    return json.loads(text[start : end + 1])


def run_tests(project_root: Path, python_exe: str, target: str) -> subprocess.CompletedProcess:
    cmd = [python_exe, "-m", "pytest", target, "-v", "--tb=short"]
    proc = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    proc.sdd_command = cmd  # carried into the test log for traceability
    return proc


def _code_version(project_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def apply_and_verify(
    task: str,
    context: dict,
    project_root: Optional[Path] = None,
    records_dir: Optional[Path] = None,
    python_exe: Optional[str] = None,
) -> EditResult:
    root = project_root or settings.project_root
    python_exe = python_exe or sys.executable
    records = records_dir or (Path(__file__).resolve().parent.parent / "records")
    records.mkdir(parents=True, exist_ok=True)

    messages = build_prompt(task, context, root)
    started = time.time()
    try:
        raw = _call_minimax(messages)
    except Exception as exc:  # noqa: BLE001
        return EditResult(ok=False, error=f"MiniMax API call failed: {exc}")
    elapsed = time.time() - started

    try:
        parsed = _extract_json(raw)
        files: Dict[str, str] = parsed["files"]
    except Exception as exc:  # noqa: BLE001
        return EditResult(ok=False, error=f"bad model output: {exc}", raw_response=raw)

    backups: Dict[Path, str] = {}
    changed: List[str] = []
    for rel, content in files.items():
        path = (root / rel).resolve()
        if not str(path).startswith(str(root.resolve())):
            return EditResult(ok=False, error=f"refusing to write outside project: {rel}")
        backups[path] = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        changed.append(rel)

    target_proc = run_tests(root, python_exe, "tests/test_orders_pagination.py")
    regression_proc = run_tests(root, python_exe, "tests/test_orders_regression.py")
    target_ok = target_proc.returncode == 0
    regression_ok = regression_proc.returncode == 0

    if not (target_ok and regression_ok):
        for path, old in backups.items():
            path.write_text(old, encoding="utf-8")

    code_version = _code_version(root)
    log = (
        f"code version (git HEAD): {code_version}\n"
        f"=== target tests ===\n$ {' '.join(target_proc.sdd_command)}\n"
        f"exit code: {target_proc.returncode}\n{target_proc.stdout}\n"
        f"=== regression tests ===\n$ {' '.join(regression_proc.sdd_command)}\n"
        f"exit code: {regression_proc.returncode}\n{regression_proc.stdout}"
    )
    result = EditResult(
        ok=target_ok and regression_ok,
        summary=parsed.get("summary", ""),
        changed_files=changed,
        target_tests_passed=target_ok,
        regression_tests_passed=regression_ok,
        test_log=log,
        raw_response=raw,
    )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    (records / f"minimax-dev-record-{stamp}.json").write_text(
        json.dumps(
            {
                "task": task,
                "model": settings.minimax_model,
                "code_version": code_version,
                "elapsed_seconds": round(elapsed, 1),
                "prompt_messages": messages,
                "raw_response": raw,
                "changed_files": changed,
                "summary": result.summary,
                "target_tests_passed": target_ok,
                "regression_tests_passed": regression_ok,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (records / f"test-log-{stamp}.txt").write_text(log, encoding="utf-8")
    return result
