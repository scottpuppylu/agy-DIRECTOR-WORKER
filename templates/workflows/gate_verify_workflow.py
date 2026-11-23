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
import pathlib
import shutil

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
py_exe = shutil.which("python3") or shutil.which("python") or "python3"

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
scope_cmd = f'"{py_exe}" -B -I -X utf8 "{tools / "scope_check.py"}" --order "{order}" --repo "{repo}" --candidate "{candidate}"'
match_cmd = (
    f'"{py_exe}" -B -I -X utf8 "{tools / "gate_check.py"}" --match "{report}" "{order}" '
    f'--evidence-dir "{evidence_dir}" --dispatch-log "{dispatch_log}" --repo "{repo}" --candidate "{candidate}"'
)

async def run_checked(cmd):
    try:
        return await call_tool("run_command", CommandLine=cmd, toolSummary="Run gate verification command", WaitMsBeforeAsync=10000)
    except ToolError as err:
        return f"TOOL_ERROR: {err.message}"

scope_out, match_out = await parallel([scope_cmd, match_cmd], run_checked)
log("SCOPE_CHECK:\n" + scope_out)
log("GATE_MATCH:\n" + match_out)

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
{"scope_output": scope_out, "gate_match_output": match_out, "blind_review": review}
