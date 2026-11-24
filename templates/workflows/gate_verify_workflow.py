"""Antigravity run_workflow template: parallel gate verification + read-only blind review.

Invoke via Antigravity `run_workflow` tool with:
  Args = {
    "order": "<abs path to WO-xxx.md>",
    "report": "<abs path to TASK_REPORT.md>",
    "repo": "<abs path to worker git checkout>",
    "director": "<abs path to DIRECTOR folder>",
    "candidate": "<40-char git HEAD SHA>"
  }
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

order = args["order"]
report = args["report"]
repo = args["repo"]
director = args["director"]
candidate = args["candidate"]

tools = pathlib.Path(director) / "tools"
if not tools.is_dir():
    tools = pathlib.Path(WORKSPACES[0]) / "tools"

evidence_dir = pathlib.Path(director) / "evidence"
dispatch_log = pathlib.Path(director) / "DIRECTOR_LOG.md"


def _find_working_python():
    """Return a verified Python executable path, skipping WindowsApps Microsoft Store stubs."""
    cands = []
    for raw in (sys.executable, shutil.which("python"), shutil.which("python3"), "python", "python3"):
        if not raw:
            continue
        p_str = str(raw)
        if os.name == "nt" and "windowsapps" in p_str.lower():
            continue
        if p_str not in cands:
            cands.append(p_str)
    for cand in cands:
        try:
            proc = subprocess.run([cand, "-B", "--version"], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                return pathlib.Path(cand).as_posix() if os.path.isabs(cand) else cand
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("no working python executable found (Windows Store stub paths skipped)")


py_exe = _find_working_python()
ps_prefix = "& " if os.name == "nt" else ""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "suggested_verdict": {"type": "string", "enum": ["PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED"]},
        "p0_issues": {"type": "array", "items": {"type": "string"}},
        "p1_issues": {"type": "array", "items": {"type": "string"}},
        "checked_files": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["suggested_verdict", "p0_issues", "p1_issues", "checked_files", "summary"],
}

phase("Mechanical Gates")
scope_cmd = f'{ps_prefix}"{py_exe}" -B -I -X utf8 "{tools / "scope_check.py"}" --order "{order}" --repo "{repo}" --candidate "{candidate}"'
match_cmd = (
    f'{ps_prefix}"{py_exe}" -B -I -X utf8 "{tools / "gate_check.py"}" --match "{report}" "{order}" '
    f'--evidence-dir "{evidence_dir}" --dispatch-log "{dispatch_log}" --repo "{repo}" --candidate "{candidate}"'
)


async def run_checked(cmd):
    try:
        out = await call_tool("run_command", CommandLine=cmd, toolSummary="Run gate verification command", WaitMsBeforeAsync=10000)
        text = str(out)
        m = re.match(r"^[ \t]*Command exited with code (-?\d+)\b", text)
        if m and int(m.group(1)) != 0:
            return {"ok": False, "exit_code": int(m.group(1)), "output": text}
        return {"ok": True, "exit_code": 0, "output": text}
    except ToolError as err:
        return {"ok": False, "exit_code": 1, "output": f"TOOL_ERROR: {err.message}"}


scope_res, match_res = await parallel([scope_cmd, match_cmd], run_checked)
mech_status = "PASS" if (scope_res["ok"] and match_res["ok"]) else "FAIL"
log(f"MECHANICAL_GATE_STATUS={mech_status} (scope_rc={scope_res['exit_code']}, match_rc={match_res['exit_code']})")
log("SCOPE_CHECK:\n" + scope_res["output"])
log("GATE_MATCH:\n" + match_res["output"])

phase("Read-Only Blind Review")
review = await agent(
    prompt=(
        f"You are a physically read-only blind reviewer.\n"
        f"1. Read the work order at `{order}` (focus on ACCEPTANCE and Section E boundary rules).\n"
        f"2. Inspect only the deliverable files in `{repo}` (do NOT trust worker prose claims).\n"
        f"3. Return structured findings listing P0/P1 issues with exact file paths and line numbers."
    ),
    schema=VERDICT_SCHEMA,
    role="Blind Reviewer",
    type_name="dw-blind-reviewer",
    workspace="inherit",
)
log("BLIND_REVIEW:\n" + json.dumps(review, indent=2, ensure_ascii=False))
{
    "mechanical_gate_status": mech_status,
    "scope_ok": scope_res["ok"],
    "scope_exit_code": scope_res["exit_code"],
    "scope_output": scope_res["output"],
    "match_ok": match_res["ok"],
    "match_exit_code": match_res["exit_code"],
    "gate_match_output": match_res["output"],
    "blind_review": review,
}
