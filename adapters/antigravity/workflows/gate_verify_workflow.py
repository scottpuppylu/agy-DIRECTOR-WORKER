"""Parallel gate_check verification workflow for Antigravity (`run_workflow`).

Runs the independent read-only gate verification suite (`--order`, `--report`, `--match`,
and `scope_check.py` / `handoff.py verify`) in parallel, preserving the P0 exit-line
guard from `af0e859`: never assume PASS unless exactly one exit-code line matches `0`.
"""
import os
import pathlib
import re
import shutil
import sys

EXIT_LINE = re.compile(r"^[ \t]*(?:Command exited with code|Exit code:|EXIT_CODE[ \t]*[:=]|exit=)[ \t]*(-?\d+)\b", re.M | re.I)


def pick_python():
    """Return a usable, non-stub Python invocation for local checks across Windows/POSIX."""
    if sys.executable and pathlib.Path(sys.executable).is_file():
        exe = pathlib.Path(sys.executable).as_posix()
        if os.name == "nt" and "WindowsApps" in exe and "python3" in exe.lower():
            exe = None
        if exe:
            return f'& "{exe}"' if (os.name == "nt" and " " in exe) else f'"{exe}"'
    for cand in ("python", "python3"):
        found = shutil.which(cand)
        if not found:
            continue
        if os.name == "nt" and "WindowsApps" in found and cand == "python3":
            continue
        if os.name == "nt" and " " in found:
            return f'& "{pathlib.Path(found).as_posix()}"'
        return cand
    return "python"


def resolve_tools_dir(repo_root, explicit_tools=None):
    """Locate `DIRECTOR/tools/` inside repo (with fallback to `tools/` when self-hosting)."""
    if explicit_tools:
        return pathlib.Path(explicit_tools).as_posix()
    r = pathlib.Path(repo_root or ".").resolve()
    for cand in (r / "DIRECTOR" / "tools", r / "tools"):
        if (cand / "gate_check.py").is_file():
            return cand.as_posix()
    return "DIRECTOR/tools"


async def run_checked(cmd):
    """Run one shell check via `call_tool('run_command', ...)` and parse exit status fail-closed."""
    res = await call_tool("run_command", {"CommandLine": cmd, "WaitMsBeforeAsync": 10000})
    text = res if isinstance(res, str) else str(res)
    codes = {int(code) for code in EXIT_LINE.findall(text)}
    if len(codes) != 1:
        why = "no exit line (still running, or output format changed)" if not codes else f"ambiguous exit codes {sorted(codes)}"
        return {"ok": False, "exit_code": None, "output": f"INVALID: {why}\n{text}"}
    code = codes.pop()
    return {"ok": code == 0, "exit_code": code, "output": text}


async def run():
    repo = args.get("repo", ".")
    order = args["order"]
    report = args["report"]
    candidate = args["candidate"]
    base_ref = args.get("base_ref", "main")
    evidence_dir = args.get("evidence_dir")
    dispatch_log = args.get("dispatch_log")
    director_dir = args.get("director") or pathlib.Path(repo, "DIRECTOR").as_posix()
    tools_dir = resolve_tools_dir(repo, args.get("tools_dir"))
    py = pick_python()
    base = f'{py} -B -I -X utf8 "{tools_dir}/gate_check.py"'

    match_cmd = f'{base} --match "{report}" "{order}" --candidate "{candidate}"'
    if evidence_dir:
        match_cmd += f' --evidence-dir "{evidence_dir}"'
    if dispatch_log:
        match_cmd += f' --dispatch-log "{dispatch_log}"'

    tasks = [
        run_checked(f'{base} --order "{order}" --repo "{repo}"'),
        run_checked(f'{base} --report "{report}"'),
        run_checked(match_cmd),
        run_checked(f'{py} -B -I -X utf8 "{tools_dir}/scope_check.py" --order "{order}" --base "{base_ref}" --candidate "{candidate}" --repo "{repo}"'),
    ]
    if (pathlib.Path(director_dir) / "INDEX.md").is_file() and (pathlib.Path(tools_dir) / "handoff.py").is_file():
        tasks.append(run_checked(f'{py} -B -I -X utf8 "{tools_dir}/handoff.py" verify --director "{director_dir}"'))

    results = await parallel(*tasks)
    names = ["order", "report", "match", "scope"]
    if (pathlib.Path(director_dir) / "INDEX.md").is_file() and (pathlib.Path(tools_dir) / "handoff.py").is_file():
        names.append("handoff_verify")
    summary = {nm: r for nm, r in zip(names, results)}
    summary["all_ok"] = all(r["ok"] for r in results)
    return summary
