"""Token Dashboard check harness — the loop's single verification entry point.

Implements docs/agent_loop_rules.md's CHECK step and the LoopKit check contract:
run every check in order, exit 0 iff all pass, and write compact evidence to
.agent/ (tails only) with full logs in .agent/raw/. Nothing feature-specific
lives here — the assertions live in tests/.

Scope skips:
  TD_ONLY=unit,smoke   run only the named checks
  TD_SKIP_SMOKE=1      skip the server import/smoke check
"""
from datetime import datetime
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / ".agent"
RAW_DIR = AGENT_DIR / "raw"
TEST_REPORT = AGENT_DIR / "test_report.md"

PASS_TAIL_LINES = 15
FAIL_TAIL_LINES = 120

ONLY_ENV = "TD_ONLY"
SKIP_SMOKE_ENV = "TD_SKIP_SMOKE"

# A stdlib-only smoke: init a throwaway DB and build the request handler, so an
# import error, schema regression, or bad migration fails fast and separately
# from the (slower) full unittest suite.
_SMOKE = (
    "import tempfile, os;"
    "from token_dashboard.db import init_db;"
    "from token_dashboard.server import build_handler;"
    "p=os.path.join(tempfile.mkdtemp(),'smoke.db');"
    "init_db(p); build_handler(p, 'x');"
    "print('smoke ok')"
)

# (key, name, command list, cwd, skipped_when_env)
CHECKS = [
    ("unit", "Unit tests", [sys.executable, "-m", "unittest", "discover", "tests"], ROOT, None),
    ("smoke", "Server import + handler build smoke", [sys.executable, "-c", _SMOKE], ROOT, SKIP_SMOKE_ENV),
]


def tail(text, keep):
    lines = (text or "").splitlines()
    if len(lines) <= keep:
        return text or ""
    return "\n".join([f"[... {len(lines) - keep} earlier lines omitted — full output in .agent/raw/ ...]",
                      *lines[-keep:]])


def run(key, name, command, cwd):
    proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return {"key": key, "name": name, "command": " ".join(map(str, command)),
            "return_code": proc.returncode, "passed": proc.returncode == 0,
            "output": output}


def git(args):
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
        return (r.stdout or "").strip() or "(clean)"
    except Exception as e:
        return f"(git unavailable: {e})"


def write_report(results):
    all_passed = all(r["passed"] for r in results)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Test Report", "",
             f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
             f"Status: {'PASSED' if all_passed else 'FAILED'}", "",
             "| Check | Result |", "| --- | --- |"]
    lines += [f"| {r['name']} | {'PASS' if r['passed'] else 'FAIL'} |" for r in results]
    lines += ["", "Full untruncated output per check: `.agent/raw/<check>.log`", ""]
    for r in results:
        (RAW_DIR / f"{r['key']}.log").write_text(r["output"], encoding="utf-8")
        keep = PASS_TAIL_LINES if r["passed"] else FAIL_TAIL_LINES
        lines += [f"## {r['name']}", "", f"Command: `{r['command']}`",
                  f"Return code: {r['return_code']}", "",
                  f"Output (tail; full log at `.agent/raw/{r['key']}.log`):", "",
                  "~~~text", tail(r["output"], keep), "~~~", ""]
    TEST_REPORT.write_text("\n".join(lines), encoding="utf-8")
    return all_passed


def write_handoff(path, intro):
    content = [intro, "",
               "# Latest Test Report", "",
               TEST_REPORT.read_text(encoding="utf-8"), "",
               "## Current step plan: `step_plan.md` (read the current step from it directly)", "",
               "## git status --short", "", "~~~text", git(["status", "--short"]), "~~~", "",
               "## git diff --stat", "", "~~~text", git(["diff", "--stat"]), "~~~"]
    path.write_text("\n".join(content), encoding="utf-8")


def main():
    AGENT_DIR.mkdir(exist_ok=True)
    checks = CHECKS
    only = os.environ.get(ONLY_ENV)
    if only:
        wanted = {k.strip() for k in only.split(",")}
        checks = [c for c in checks if c[0] in wanted]
    checks = [c for c in checks if not (c[4] and os.environ.get(c[4]))]
    if not checks:
        print("No checks selected/configured.")
        return 1

    results = []
    for key, name, command, cwd, _skip in checks:
        print(f"=== {name} ===")
        r = run(key, name, command, cwd)
        print(f"--- {name}: {'PASS' if r['passed'] else 'FAIL'} (exit {r['return_code']})")
        results.append(r)

    if write_report(results):
        write_handoff(AGENT_DIR / "review_input.md",
                      "# Review Input\n\nAll checks passed. Review this step against its "
                      "acceptance criteria in step_plan.md and the section it names in README.md/docs.")
        print("All checks passed. -> .agent/review_input.md")
        return 0

    write_handoff(AGENT_DIR / "debugger_input.md",
                  "# Debugger Input\n\nRules: fix only the failing behavior for the current "
                  "step; don't edit tests unless clearly wrong; respect docs/BOUNDARIES.md; "
                  "the check re-runs automatically when you drop the sentinel again.")
    print("Checks FAILED. -> .agent/debugger_input.md")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
