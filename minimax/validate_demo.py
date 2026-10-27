"""End-to-end validation for the SDD context module (TASK-001).

Runs the full acceptance flow and writes a step-by-step log:

  step 0  reset demo project to initial state (pre-change snapshot)
  step 1  import specs/code/tests into seekdb
  step 2  target tests FAIL before the change (feature missing, not env error)
  step 3  apply spec update v1.2, re-import, context now contains PS-1..PS-5
  step 4  build context and call MiniMax to modify the code
  step 5  target tests PASS, regression tests PASS
  step 6  task state saved to PowerContext; restart-safe read-back
  step 7  spec update v1.3 + re-import -> context reflects PS-6 (not fixed answers)

Usage:
    python validate_demo.py            # full flow (requires MINIMAX_API_KEY)
    python validate_demo.py --dry-run  # everything except the MiniMax call
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE / "demo_project"
SPECS = PROJECT / "specs"
UPDATES = PROJECT / "spec_updates"
LOG_DIR = HERE / "records"
SNAPSHOT_BEFORE = HERE / "snapshots" / "before"
SNAPSHOT_AFTER = HERE / "snapshots" / "after"

STEP_RESULTS: list[dict] = []


def log(step: str, ok: bool, detail: str) -> None:
    entry = {"step": step, "ok": ok, "detail": detail}
    STEP_RESULTS.append(entry)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {step}: {detail}")


def run_py(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, *args], cwd=cwd, capture_output=True, text=True,
        timeout=600, env=env, encoding="utf-8", errors="replace",
    )


def parse_json(proc: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        print(f"  !! non-JSON output (exit={proc.returncode}): {(proc.stderr or proc.stdout or '')[-400:]}")
        return {}


def snapshot(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        PROJECT, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )


def reset_project() -> None:
    """Restore the pre-change state of the demo project from the before snapshot."""
    core = PROJECT / "ordersvc" / "core.py"
    shutil.copy(SNAPSHOT_BEFORE / "ordersvc" / "core.py", core)
    shutil.copy(
        SNAPSHOT_BEFORE / "specs" / "SPEC-001-order-query.md",
        SPECS / "SPEC-001-order-query.md",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="skip the MiniMax API call")
    args = parser.parse_args()
    LOG_DIR.mkdir(exist_ok=True)

    if not SNAPSHOT_BEFORE.exists():
        print("creating initial 'before' snapshot from current project state")
        snapshot(SNAPSHOT_BEFORE)

    # step 0: reset
    reset_project()
    log("step0-reset", True, "demo project restored to pre-change state")

    # step 1: import materials
    proc = run_py(["-m", "sddctx", "import"], HERE)
    ok = proc.returncode == 0
    log("step1-import", ok, (proc.stdout or proc.stderr).strip()[-300:])

    # step 2: target tests must fail (feature not implemented)
    proc = run_py(["-m", "pytest", "tests/test_orders_pagination.py", "-q"], PROJECT)
    failed_as_expected = proc.returncode != 0 and "error" not in proc.stdout.split("\n")[-1].lower()
    log(
        "step2-target-tests-fail-before",
        failed_as_expected,
        f"exit={proc.returncode}, {proc.stdout.strip().splitlines()[-1] if proc.stdout else ''}",
    )
    proc = run_py(["-m", "pytest", "tests/test_orders_regression.py", "-q"], PROJECT)
    log("step2b-regression-pass-before", proc.returncode == 0, f"exit={proc.returncode}")

    # step 3: spec update v1.2 -> re-import -> context contains PS rules
    shutil.copy(UPDATES / "SPEC-001-order-query-v1.2.md", SPECS / "SPEC-001-order-query.md")
    proc = run_py(["-m", "sddctx", "import"], HERE)
    log("step3-reimport-v12", proc.returncode == 0, "spec v1.2 imported")
    task_text = (HERE / "tasks" / "TASK-001.md").read_text(encoding="utf-8")
    proc = run_py(
        ["-m", "sddctx", "context", "--task", task_text, "--top-k", "8", "--json", "--no-state"],
        HERE,
    )
    ctx = parse_json(proc) if proc.returncode == 0 else {}
    spec_symbols = {h["symbol"] for h in ctx.get("specs", [])}
    has_ps = any(s.startswith("PS-") for s in spec_symbols)
    log("step3-context-has-ps-rules", has_ps, f"spec hits: {sorted(spec_symbols)[:8]}")

    # step 4+5: MiniMax modifies code; tests must pass afterwards
    if args.dry_run:
        log("step4-minimax-edit", True, "skipped (--dry-run)")
    else:
        proc = run_py(
            ["-m", "sddctx", "run", "--task-id", "TASK-001", "--task", task_text], HERE
        )
        result = parse_json(proc)
        log(
            "step4-minimax-edit",
            bool(result.get("ok")),
            f"changed={result.get('changed_files')}, summary={result.get('summary', '')[:120]}",
        )
        proc = run_py(["-m", "pytest", "tests/test_orders_pagination.py", "-q"], PROJECT)
        log("step5-target-tests-pass-after", proc.returncode == 0, f"exit={proc.returncode}")
        proc = run_py(["-m", "pytest", "tests/test_orders_regression.py", "-q"], PROJECT)
        log("step5b-regression-pass-after", proc.returncode == 0, f"exit={proc.returncode}")
        snapshot(SNAPSHOT_AFTER)

    # step 6: PowerContext state read-back (restart-safe)
    proc = run_py(["-m", "sddctx", "state", "--task-id", "TASK-001"], HERE)
    state_ok = proc.returncode == 0 and '"entries"' in proc.stdout
    entries = []
    if state_ok:
        try:
            entries = json.loads(proc.stdout).get("entries", [])
        except json.JSONDecodeError:
            state_ok = False
    log("step6-state-readback", state_ok and (args.dry_run or len(entries) > 0),
        f"{len(entries)} entries for TASK-001")

    # step 7: spec v1.3 -> re-import -> context reflects PS-6
    shutil.copy(UPDATES / "SPEC-001-order-query-v1.3.md", SPECS / "SPEC-001-order-query.md")
    run_py(["-m", "sddctx", "import"], HERE)
    proc = run_py(
        ["-m", "sddctx", "context", "--task", "分页 page_size 最大值限制", "--top-k", "8", "--json", "--no-state"],
        HERE,
    )
    ctx = parse_json(proc) if proc.returncode == 0 else {}
    has_ps6 = any("PS-6" in h["symbol"] or "PS-6" in h["text"] for h in ctx.get("specs", []))
    log("step7-context-reflects-v13", has_ps6, "PS-6 present in retrieved context" if has_ps6 else "PS-6 missing")

    # restore spec to v1.2 state used during the run
    shutil.copy(UPDATES / "SPEC-001-order-query-v1.2.md", SPECS / "SPEC-001-order-query.md")
    run_py(["-m", "sddctx", "import"], HERE)

    stamp = date.today().isoformat()
    (LOG_DIR / f"validation-log-{stamp}.json").write_text(
        json.dumps(STEP_RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    all_ok = all(e["ok"] for e in STEP_RESULTS)
    print("\nOVERALL:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
