"""CLI entry point for the SDD context module.

Usage:
    python -m sddctx import                      # (re)import specs/code/tests into seekdb
    python -m sddctx context --task "<任务描述>"   # build development context for a task
    python -m sddctx run --task-id TASK-001 --task "<任务描述>"
                                                 # full flow: context -> MiniMax edit -> tests -> save state
    python -m sddctx state [--task-id ID]        # read saved task state (works after restart)
    python -m sddctx stats                       # store statistics
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import settings


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_import(args) -> int:
    from .store import ContextStore

    store = ContextStore()
    root = Path(args.project) if args.project else settings.project_root
    result = store.ingest(root)
    _print(result)
    return 0


def _build_context(task: str, top_k: int, with_state: bool) -> dict:
    from .store import ContextStore

    store = ContextStore()
    context = {
        "task": task,
        "specs": store.retrieve(task, top_k=top_k, kinds=["spec"]),
        "code": store.retrieve(task, top_k=top_k, kinds=["code"]),
        "tests": store.retrieve(task, top_k=top_k, kinds=["test"]),
    }
    if with_state:
        try:
            from .state import StateStore

            state = StateStore()
            state.ensure_scope()
            context["state"] = state.load_state()
        except Exception as exc:  # noqa: BLE001
            context["state"] = []
            context["state_error"] = str(exc)
    return context


def cmd_context(args) -> int:
    context = _build_context(args.task, args.top_k, not args.no_state)
    if args.json:
        _print(context)
    else:
        print(f"任务: {context['task']}\n")
        for section, key in (("适用规格条目", "specs"), ("需要修改的代码", "code"), ("验证测试", "tests")):
            print(f"== {section} ==")
            for hit in context[key]:
                print(f"  [{hit['kind']}] {hit['source']} :: {hit['symbol']} (distance={hit['distance']:.3f})")
            print()
        if context.get("state"):
            print("== 已保存的任务状态（PowerContext）==")
            for entry in context["state"]:
                print(f"  {json.dumps(entry, ensure_ascii=False)[:160]}")
        elif "state_error" in context:
            print(f"== 任务状态不可用: {context['state_error']}")
    return 0


def cmd_run(args) -> int:
    from .minimax_editor import apply_and_verify

    context = _build_context(args.task, args.top_k, True)
    result = apply_and_verify(args.task, context)
    if result.ok:
        try:
            from .state import StateStore

            state = StateStore()
            state.ensure_scope()
            state.save_progress(
                args.task_id,
                {
                    "reason": "task completed",
                    "status": "done",
                    "summary": result.summary,
                    "changed_files": result.changed_files,
                    "target_tests_passed": result.target_tests_passed,
                    "regression_tests_passed": result.regression_tests_passed,
                },
            )
            state.save_evidence(
                args.task_id,
                {"type": "test-log", "log": result.test_log[-4000:]},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"warning: failed to persist state to PowerContext: {exc}", file=sys.stderr)
    _print(
        {
            "ok": result.ok,
            "summary": result.summary,
            "changed_files": result.changed_files,
            "target_tests_passed": result.target_tests_passed,
            "regression_tests_passed": result.regression_tests_passed,
            "error": result.error,
        }
    )
    return 0 if result.ok else 1


def cmd_state(args) -> int:
    from .state import StateStore

    state = StateStore()
    state.ensure_scope()
    _print({"scope_id": state.scope_id, "entries": state.load_state(args.task_id)})
    return 0


def cmd_stats(args) -> int:
    from .store import ContextStore

    _print(ContextStore().stats())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sddctx", description="SDD context management module")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="import specs/code/tests into seekdb")
    p_import.add_argument("--project", help="project root (default: demo_project)")
    p_import.set_defaults(func=cmd_import)

    p_ctx = sub.add_parser("context", help="build development context for a task")
    p_ctx.add_argument("--task", required=True)
    p_ctx.add_argument("--top-k", type=int, default=6)
    p_ctx.add_argument("--no-state", action="store_true", help="skip PowerContext state")
    p_ctx.add_argument("--json", action="store_true")
    p_ctx.set_defaults(func=cmd_context)

    p_run = sub.add_parser("run", help="context -> MiniMax edit -> tests -> save state")
    p_run.add_argument("--task-id", required=True)
    p_run.add_argument("--task", required=True)
    p_run.add_argument("--top-k", type=int, default=6)
    p_run.set_defaults(func=cmd_run)

    p_state = sub.add_parser("state", help="read saved task state")
    p_state.add_argument("--task-id")
    p_state.set_defaults(func=cmd_state)

    p_stats = sub.add_parser("stats", help="store statistics")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
