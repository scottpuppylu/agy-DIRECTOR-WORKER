#!/usr/bin/env python3
"""Environment & runtime-guard self-check for agy-DIRECTOR-WORKER (agydw). Reads only; writes nothing.

Checks:
  python / git / gh   toolchain prerequisites
  os_sandbox          Windows 11 24H2+/AppContainer (build >= 26100), macOS seatbelt, Linux userns
  agy_cli             Antigravity CLI (`agy`) availability (optional INFO when using App-only)
  transcripts         Antigravity brain root discovery and latest transcript.jsonl parseability
  hooks               .agents/hooks.json syntax + live dry-run probe of agy_guard.py (deny/allow)
  plugin              plugin.json schema, rules/AGENTS.md <= 24,000 bytes, skills, valid agent tools
  project_rules       AGENTS.md / GEMINI.md size check (<= 24,000 bytes per file)
  tools               presence of core + agy_* scripts in tools/
  config              optional director_work.json validation (--config)
  licenses            optional license expiry check (--licenses FILE, DW 2.3.0 parity)

Exit codes: 0 no FAIL, 1 any FAIL, 2 input/usage error. Never modifies ~/.gemini/config/config.json.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

REQUIRED_TOOLS = (
    "agy_guard.py", "agy_audit.py", "agy_hook_bridge.py", "agy_usage.py",
    "agy_doctor.py", "agy_setup.py", "agy_setup_check.py", "safety_gate.py",
    "gate_check.py", "scope_check.py", "evidence_record.py", "audit_log.py",
    "loop_guard.py", "inject_scan.py", "resume_inject.py", "report_vs_audit.py",
    "memory_size_check.py", "guard_rules.json", "vocabulary.json",
)

# Verified Antigravity 2.0 agent YAML tool names (antigravity.google/docs/subagents + local plugin schema)
KNOWN_GOOD_AGENT_TOOLS = {
    "read_file", "edit_file", "write_file", "run_command", "manage_task", "search_web",
    "schedule", "invoke_subagent", "define_subagent", "send_message", "manage_subagents",
    "ask_question", "update_focus", "update_steps", "call_mcp_tool", "list_resources", "read_resource",
    "swarm", "post_message", "read_board", "swarm_wait",
}
HANG_RISK_AGENT_TOOLS = {"view_file", "write_to_file", "replace_file_content", "multi_replace_file_content", "grep_search", "find_by_name"}
ALLOWED_PLUGIN_FIELDS = {"name", "description", "logo", "suggestedPrompts", "displayName", "version", "author", "homepage", "repository", "disabled"}


def check_item(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def check_os_sandbox():
    """Evaluate native Antigravity Terminal Sandbox support on this OS."""
    if os.name == "nt":
        ver = sys.getwindowsversion()
        # Antigravity Windows AppContainer sandbox requires Windows 11 24H2+ (build >= 26100) + Aug 2026 update
        if ver.major >= 10 and ver.build >= 26100:
            return check_item(
                "os_sandbox",
                "CONFIGURED_NOT_VERIFIED",
                f"Windows build {ver.build} (>= 26100 / 24H2+): AppContainer sandbox supported (requires Default/Auto preset in App; non-profile drives stay readable)",
            )
        return check_item(
            "os_sandbox",
            "NOT_ENFORCED",
            f"Windows build {ver.build} (< 26100): native AppContainer sandbox unavailable; use Request Review + workspace: branch",
        )
    if sys.platform == "darwin":
        sb = shutil.which("sandbox-exec")
        return check_item("os_sandbox", "CONFIGURED_NOT_VERIFIED" if sb else "NOT_ENFORCED",
                          f"macOS Seatbelt ({sb or 'sandbox-exec missing'})")
    # Linux
    ns_ok = pathlib.Path("/proc/self/ns/user").exists()
    return check_item("os_sandbox", "CONFIGURED_NOT_VERIFIED" if ns_ok else "NOT_ENFORCED",
                      "Linux user namespaces available" if ns_ok else "/proc/self/ns/user missing")


def check_hooks_and_probe(repo, probe_hooks):
    """Validate .agents/hooks.json (if present) and live-probe agy_guard.py."""
    items = []
    hooks_path = pathlib.Path(repo) / ".agents" / "hooks.json" if repo else None
    if hooks_path:
        if hooks_path.is_file():
            try:
                data = json.loads(hooks_path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict) and data:
                    items.append(check_item("workspace_hooks", "ENFORCED", f"{hooks_path.as_posix()} valid JSON ({len(data)} hook group(s))"))
                else:
                    items.append(check_item("workspace_hooks", "WARN", f"{hooks_path.as_posix()} is empty"))
            except (OSError, ValueError) as exc:
                items.append(check_item("workspace_hooks", "FAIL", f"{hooks_path.as_posix()} invalid JSON: {exc}"))
        else:
            items.append(check_item("workspace_hooks", "MISSING", f"{hooks_path.as_posix()} not found (run agy_setup.py --apply)"))

    if probe_hooks or (hooks_path and hooks_path.is_file()):
        guard = HERE / "agy_guard.py"
        rules = HERE / "guard_rules.json"
        if not guard.is_file() or not rules.is_file():
            items.append(check_item("guard_probe", "FAIL", "agy_guard.py or guard_rules.json missing"))
            return items
        cases = [
            ("deny_force_push", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git push --force origin main"}}, "hookEventName": "PreToolUse"}, "deny"),
            ("allow_status", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git status"}}, "hookEventName": "PreToolUse"}, "allow"),
            ("deny_env_read", {"toolCall": {"name": "read_file", "args": {"Path": ".env"}}, "hookEventName": "PreToolUse"}, "deny"),
        ]
        for cname, payload, expected in cases:
            proc = subprocess.run(
                [sys.executable, "-B", "-I", "-X", "utf8", str(guard), "--rules", str(rules), "--profile", "worker", "--mode", "block"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            try:
                res = json.loads(proc.stdout.strip())
                got = res.get("decision")
            except ValueError:
                got = None
            if proc.returncode == 0 and got == expected:
                continue
            items.append(check_item("guard_probe", "FAIL", f"{cname}: expected {expected} (rc=0), got {got!r} (rc={proc.returncode})"))
            return items
        items.append(check_item("guard_probe", "ENFORCED", "agy_guard.py live probe passed 3/3 (force push=deny, .env read=deny, git status=allow)"))
    return items


def check_plugin_dir(plugin_dir):
    """Validate an Antigravity plugin folder (manifest, AGENTS.md <= 24KB, skills, agent tool names)."""
    items = []
    pdir = pathlib.Path(plugin_dir)
    manifest = pdir / "plugin.json"
    if not manifest.is_file():
        return [check_item("plugin", "FAIL", f"missing {manifest.as_posix()}")]
    try:
        mdata = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [check_item("plugin", "FAIL", f"invalid {manifest.name}: {exc}")]
    extra = sorted(set(mdata) - ALLOWED_PLUGIN_FIELDS)
    if extra:
        items.append(check_item("plugin_manifest", "FAIL", f"unknown plugin.json keys dropped by loader: {extra}"))
    elif not mdata.get("name"):
        items.append(check_item("plugin_manifest", "FAIL", "plugin.json missing name"))
    else:
        items.append(check_item("plugin_manifest", "OK", f"plugin `{mdata['name']}` valid ({len(mdata.get('suggestedPrompts') or [])} starter prompts)"))

    rules_file = pdir / "rules" / "AGENTS.md"
    if rules_file.is_file():
        sz = rules_file.stat().st_size
        st = "OK" if sz <= 24000 else "FAIL"
        items.append(check_item("plugin_rules", st, f"{rules_file.name}: {sz} bytes (limit 24,000)"))

    for skill in sorted((pdir / "skills").glob("*/SKILL.md")) if (pdir / "skills").is_dir() else []:
        txt = skill.read_text(encoding="utf-8-sig", errors="replace")
        fm = re.match(r"^---\r?\n(.*?)\r?\n---", txt, re.S)
        if not fm:
            items.append(check_item("plugin_skill", "FAIL", f"{skill.parent.name}: missing YAML frontmatter"))
            continue
        m_name = re.search(r"^name:\s*(\S+)", fm.group(1), re.M)
        m_desc = re.search(r"^description:\s*\S+", fm.group(1), re.M)
        if not m_name or m_name.group(1) != skill.parent.name or not m_desc:
            items.append(check_item("plugin_skill", "FAIL", f"{skill.parent.name}: frontmatter name/description mismatch"))
        else:
            items.append(check_item("plugin_skill", "OK", f"skill `{skill.parent.name}` valid"))

    for agent in sorted((pdir / "agents").glob("*.md")) if (pdir / "agents").is_dir() else []:
        txt = agent.read_text(encoding="utf-8-sig", errors="replace")
        fm = re.match(r"^---\r?\n(.*?)\r?\n---", txt, re.S)
        if not fm:
            items.append(check_item("plugin_agent", "FAIL", f"{agent.name}: missing frontmatter"))
            continue
        m_tools = re.search(r"^tools:\s*\[([^\]]*)\]", fm.group(1), re.M)
        if m_tools:
            tools_list = [t.strip().strip("'\"") for t in m_tools.group(1).split(",") if t.strip()]
            risky = [t for t in tools_list if t in HANG_RISK_AGENT_TOOLS]
            if risky:
                items.append(check_item("plugin_agent", "FAIL", f"{agent.name}: unmapped App 2.0 tool(s) {risky} risk subagent hang"))
                continue
        items.append(check_item("plugin_agent", "OK", f"agent `{agent.stem}` valid"))
    return items


def check_licenses(path_str, warn_days):
    """Check tool/trial license expiry dates (DW 2.3.0 N6 parity)."""
    p = pathlib.Path(path_str)
    if not p.is_file():
        return [check_item("licenses", "FAIL", f"licenses file missing: {p}")], 0, 0, 0, 1
    try:
        raw = p.read_text(encoding="utf-8-sig")
        entries = []
        if p.suffix.lower() == ".json":
            obj = json.loads(raw)
            entries = obj.get("licenses", []) if isinstance(obj, dict) else obj
        else:
            for line in raw.splitlines():
                if line.strip().startswith("|") and "---" not in line:
                    cols = [c.strip() for c in line.strip().strip("|").split("|")]
                    if len(cols) >= 3 and cols[0].lower() != "tool":
                        entries.append({"tool": cols[0], "license": cols[1], "expires": cols[2]})
    except (OSError, ValueError) as exc:
        return [check_item("licenses", "FAIL", f"cannot parse {p}: {exc}")], 0, 0, 0, 1

    today = datetime.date.today()
    items = []
    ok = expiring = expired = bad = 0
    for e in entries:
        tool = str(e.get("tool") or "?")
        lic = str(e.get("license") or "?")
        exp_str = str(e.get("expires") or "")
        try:
            exp_date = datetime.date.fromisoformat(exp_str)
        except ValueError:
            bad += 1
            items.append(check_item("licenses", "WARN", f"unreadable expiry date for {lic} ({tool}): {exp_str!r}"))
            continue
        left = (exp_date - today).days
        if left < 0:
            expired += 1
            items.append(check_item("licenses", "WARN", f"license expired: {lic} (held by {tool}) expired {exp_str}"))
        elif left <= warn_days:
            expiring += 1
            items.append(check_item("licenses", "WARN", f"license expiring: {lic} (held by {tool}) expires {exp_str}, {left} day(s) left"))
        else:
            ok += 1
            items.append(check_item("licenses", "OK", f"{lic} ({tool}): {left} day(s) left"))
    return items, ok, expiring, expired, bad


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="target project git repository to inspect")
    ap.add_argument("--plugin", help="plugin directory to validate (default: ../plugin/director-work if present)")
    ap.add_argument("--config", help="director_work.json to validate")
    ap.add_argument("--brain-root", action="append", default=[], help="override Antigravity brain directory")
    ap.add_argument("--probe-hooks", action="store_true", help="live-probe agy_guard.py even without --repo")
    ap.add_argument("--licenses", help="JSON or Markdown license expiry table")
    ap.add_argument("--license-days", type=int, default=3, help="days threshold for license warning (default 3)")
    ap.add_argument("--licenses-only", action="store_true", help="check only --licenses and exit")
    ap.add_argument("--json", action="store_true", help="output JSON")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    if a.licenses_only:
        if not a.licenses:
            print("DOCTOR_AGY: --licenses-only requires --licenses", file=sys.stderr)
            return 2
        litems, ok, exp_ing, exp_ed, bad = check_licenses(a.licenses, a.license_days)
        for it in litems:
            print(f"{it['status']:8s} {it['name']}: {it['detail']}")
        print(f"LICENSES: ok={ok} expiring={exp_ing} expired={exp_ed} bad={bad}")
        return 1 if (exp_ing or exp_ed or bad) else 0

    items = []
    # 1. Python & Git
    pv = sys.version_info
    items.append(check_item("python", "OK" if pv >= (3, 9) else "FAIL", f"Python {pv.major}.{pv.minor}.{pv.micro}"))
    git_bin = shutil.which("git")
    items.append(check_item("git", "OK" if git_bin else "FAIL", git_bin or "git not found on PATH"))

    # 2. OS Sandbox
    items.append(check_os_sandbox())

    # 3. agy CLI (optional INFO if App-only)
    agy_bin = shutil.which("agy")
    if agy_bin:
        ver_proc = subprocess.run([agy_bin, "--version"], capture_output=True, text=True, timeout=5)
        items.append(check_item("agy_cli", "OK", f"{agy_bin} ({ver_proc.stdout.strip() or 'version ok'})"))
    else:
        items.append(check_item("agy_cli", "INFO", "agy CLI not on PATH (normal when running inside Antigravity App 2.0)"))

    # 4. Transcripts
    roots = [pathlib.Path(r) for r in a.brain_root] or [
        pathlib.Path.home() / ".gemini" / s / "brain" for s in ("antigravity", "antigravity-cli", "antigravity-ide")
    ]
    existing_roots = [r for r in roots if r.is_dir()]
    if existing_roots:
        tfs = sorted((p for r in existing_roots for p in r.glob("*/.system_generated/logs/transcript.jsonl")),
                     key=lambda p: p.stat().st_mtime, reverse=True)
        if tfs:
            try:
                first_obj = json.loads(tfs[0].read_text(encoding="utf-8", errors="replace").splitlines()[0])
                items.append(check_item("transcripts", "OK", f"{len(tfs)} conversation(s); latest step_index={first_obj.get('step_index')}"))
            except (OSError, IndexError, ValueError) as exc:
                items.append(check_item("transcripts", "WARN", f"found {tfs[0].name} but failed parse: {exc}"))
        else:
            items.append(check_item("transcripts", "INFO", f"brain root exists ({existing_roots[0].as_posix()}) with 0 transcripts yet"))
    else:
        items.append(check_item("transcripts", "INFO", "no Antigravity brain directory found on this machine"))

    # 5. Required tools
    missing_tools = [t for t in REQUIRED_TOOLS if not (HERE / t).is_file()]
    if missing_tools:
        items.append(check_item("tools", "FAIL", f"missing in tools/: {', '.join(missing_tools)}"))
    else:
        items.append(check_item("tools", "OK", f"all {len(REQUIRED_TOOLS)} required tools present"))

    # 6. Hooks & live probe
    items += check_hooks_and_probe(a.repo, a.probe_hooks)

    # 7. Plugin check
    default_plugin = HERE.parent / "plugin" / "director-work"
    pdir = pathlib.Path(a.plugin) if a.plugin else (default_plugin if default_plugin.is_dir() else None)
    if pdir:
        items += check_plugin_dir(pdir)

    # 8. Repo rules size check
    if a.repo:
        rpath = pathlib.Path(a.repo)
        for fname in ("AGENTS.md", "GEMINI.md"):
            fp = rpath / fname
            if fp.is_file():
                sz = fp.stat().st_size
                items.append(check_item("project_rules", "OK" if sz <= 24000 else "FAIL", f"{fname}: {sz} bytes (limit 24,000)"))

    # 9. Config check
    if a.config:
        cpath = pathlib.Path(a.config)
        try:
            cfg = json.loads(cpath.read_text(encoding="utf-8-sig"))
            if isinstance(cfg, dict) and cfg.get("schema_version") == 1:
                items.append(check_item("config", "OK", f"{cpath.as_posix()} schema_version=1"))
            else:
                items.append(check_item("config", "FAIL", f"{cpath.as_posix()} needs schema_version=1"))
        except (OSError, ValueError) as exc:
            items.append(check_item("config", "FAIL", f"{cpath.as_posix()}: {exc}"))

    # 10. Licenses
    if a.licenses:
        litems, *_ = check_licenses(a.licenses, a.license_days)
        items += litems

    counts = {s: sum(1 for it in items if it["status"] == s)
              for s in ("OK", "ENFORCED", "CONFIGURED_NOT_VERIFIED", "NOT_ENFORCED", "MISSING", "WARN", "FAIL", "INFO")}
    summary = {"status": "FAIL" if counts["FAIL"] else "OK", "counts": counts, "checks": items}

    if a.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for it in items:
            print(f"{it['status']:24s} {it['name']}: {it['detail']}")
        print(f"DOCTOR_AGY: ok={counts['OK'] + counts['ENFORCED']} warn={counts['WARN'] + counts['NOT_ENFORCED'] + counts['MISSING']} "
              f"not_verified={counts['CONFIGURED_NOT_VERIFIED']} fail={counts['FAIL']}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
