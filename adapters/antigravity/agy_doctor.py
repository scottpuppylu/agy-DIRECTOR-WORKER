#!/usr/bin/env python3
"""Environment, hooks, plugin & .agent/rules/ diagnostics for agy-DIRECTOR-WORKER (3.0.0-agy.7)."""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agy_doc_common", HERE / "agy_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()

REQUIRED_BASE_TOOLS = (
    "_common.py", "spec_gate.py", "safety_gate.py", "gate_check.py", "scope_check.py",
    "evidence_record.py", "audit_log.py", "loop_guard.py", "inject_scan.py",
    "resume_inject.py", "report_vs_audit.py", "memory_size_check.py", "install.py",
    "bootstrap_repo.py", "handoff.py", "privacy_scan.py", "guard_rules.json", "vocabulary.json",
)
REQUIRED_ADAPTER_FILES = (
    "agy_common.py", "spec_gate_hook.py", "GATE_RULES.json", "agy_guard.py",
    "agy_audit.py", "agy_hook_bridge.py", "agy_usage.py", "agy_doctor.py",
    "agy_setup.py", "agy_setup_check.py", "agy_version.py", "base_sync_check.py",
    "VARIANT.json", "BASE_SYNC.json",
)
KNOWN_GOOD_AGENT_TOOLS = {
    "read_file", "edit_file", "write_file", "run_command", "manage_task", "search_web",
    "schedule", "invoke_subagent", "define_subagent", "send_message", "manage_subagents",
    "ask_question", "update_focus", "update_steps", "call_mcp_tool", "list_resources", "read_resource",
    "swarm", "post_message", "read_board", "swarm_wait",
}
HANG_RISK_AGENT_TOOLS = {"view_file", "write_to_file", "replace_file_content", "multi_replace_file_content", "grep_search", "find_by_name"}
ALLOWED_PLUGIN_FIELDS = {
    "$schema", "name", "displayName", "display_name", "description", "version", "disabled",
    "logo", "suggestedPrompts", "suggested_prompts", "skills", "author", "homepage",
    "repository", "license", "keywords", "extensions", "source", "sourcePath", "source_path",
}
PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
NON_WS_RE = re.compile(r"\S")


def check_item(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def check_os_sandbox():
    if os.name == "nt":
        ver = sys.getwindowsversion()
        if ver.major >= 10 and ver.build >= 26100:
            return check_item("os_sandbox", "CONFIGURED_NOT_VERIFIED", f"Windows build {ver.build} (>= 26100): AppContainer sandbox supported")
        return check_item("os_sandbox", "NOT_ENFORCED", f"Windows build {ver.build} (< 26100): use Request Review + workspace: branch")
    if sys.platform == "darwin":
        sb = shutil.which("sandbox-exec")
        return check_item("os_sandbox", "CONFIGURED_NOT_VERIFIED" if sb else "NOT_ENFORCED", f"macOS Seatbelt ({sb or 'sandbox-exec missing'})")
    ns_ok = pathlib.Path("/proc/self/ns/user").exists()
    return check_item("os_sandbox", "CONFIGURED_NOT_VERIFIED" if ns_ok else "NOT_ENFORCED", "Linux userns available" if ns_ok else "/proc/self/ns/user missing")


def _run_shell_hook(cmd_line, cwd_dir, payload, extra_env=None):
    inp = json.dumps(payload, ensure_ascii=False)
    env = dict(os.environ, **(extra_env or {}))
    try:
        if os.name == "nt":
            return subprocess.run("cmd /d /c " + cmd_line, cwd=str(cwd_dir), input=inp, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=20)
        return subprocess.run(["sh", "-c", cmd_line], cwd=str(cwd_dir), input=inp, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return exc


def _quote_probe_path(p):
    s = pathlib.Path(p).resolve().as_posix()
    if re.fullmatch(r"[A-Za-z0-9_./:{}-]+", s):
        return s
    return f'"{s}"' if os.name == "nt" else shlex.quote(s)


def _sanitize_probe_cmd(cmd_line, probe_dir):
    repl = {
        "--audit": _quote_probe_path(probe_dir / "probe_audit.jsonl"),
        "--bridge-state": _quote_probe_path(probe_dir / "probe_bridge"),
        "--pause-flag": _quote_probe_path(probe_dir / "probe_pause.flag"),
    }
    return re.sub(
        r'(--(?:audit|bridge-state|pause-flag))(=|\s+)(?:"[^"]*"|\'[^\']*\'|\S+)',
        lambda m: f"{m.group(1)}{m.group(2)}{repl[m.group(1).lower()]}",
        cmd_line,
        flags=re.I,
    )


def _probe_pretool_command(check_name, cmd_line, cwd_dir, mode="remind", hard_only=False):
    cases = [
        ("deny_env_read", {"toolCall": {"name": "read_file", "args": {"Path": ".env"}}, "hookEventName": "PreToolUse"}, "deny", None, None),
        ("deny_shell_oauth", {"toolCall": {"name": "run_command", "args": {"CommandLine": "cat ~/.gemini/oauth_creds.json"}}, "hookEventName": "PreToolUse"}, "deny", None, None),
        ("allow_status", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git status"}}, "hookEventName": "PreToolUse"}, "allow", None, None),
    ]
    if hard_only:
        g01_expected = "allow"
        cases.append(("g01_force_push_hard_only", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git push --force origin main"}}, "hookEventName": "PreToolUse"}, "allow", None, "[G01]"))
    else:
        g01_expected = "deny" if mode == "block" else ("force_ask" if mode == "ask" else "allow")
        g01_substr = "[G01]" if mode != "block" else None
        cases.append(("g01_force_push", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git push --force origin main"}}, "hookEventName": "PreToolUse"}, g01_expected, g01_substr, None))
    with tempfile.TemporaryDirectory(prefix="agydw_doctor_probe_") as td:
        probe_cmd = _sanitize_probe_cmd(cmd_line, pathlib.Path(td))
        for cname, payload, expected_dec, want_substr, forbid_substr in cases:
            proc = _run_shell_hook(probe_cmd, cwd_dir, payload, extra_env={"AGYDW_DOCTOR_PROBE": "1"})
            if isinstance(proc, Exception):
                return check_item(check_name, "FAIL", f"{cname}: {proc}")
            try:
                res = json.loads(proc.stdout.strip())
                got, reason = res.get("decision"), str(res.get("reason") or "")
            except ValueError:
                got, reason = None, ""
            if proc.returncode != 0 or got != expected_dec or (want_substr and want_substr not in reason) or (forbid_substr and forbid_substr in reason):
                return check_item(check_name, "FAIL", f"{cname}: expected {expected_dec!r}, got {got!r} ({reason[:120]}) rc={proc.returncode}")
    label = f"hard-only({mode})" if hard_only else mode
    return check_item(check_name, "HOOK_EXEC_OK", f"passed 4/4 (.env=deny, oauth_shell=deny, git status=allow, force-push {label}={g01_expected})")


def _probe_spec_gate_command(cmd_line, repo_dir):
    rp = pathlib.Path(repo_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="agydw_sg_probe_") as td:
        missing_brief = _quote_probe_path(pathlib.Path(td) / "no-brief" / "BRIEF.md")
        cmd_test = re.sub(r'(--brief)(=|\s+)(?:"[^"]*"|\'[^\']*\'|\S+)', lambda m: f"{m.group(1)}{m.group(2)}{missing_brief}", cmd_line, flags=re.I)
        dir_opt = _COMMON.extract_opt(_COMMON.split_cmd_tokens(cmd_line), "--director")
        dir_dir = pathlib.Path(dir_opt) if dir_opt else (rp / "DIRECTOR")
        if not dir_dir.is_absolute():
            dir_dir = (rp / dir_dir).resolve()
        cases = [
            ("read_file", {"toolCall": {"name": "read_file", "args": {"Path": (rp / "app.py").as_posix()}}, "cwd": rp.as_posix()}, "allow"),
            ("write_product", {"toolCall": {"name": "write_file", "args": {"Path": (rp / "dw_gate_probe.py").as_posix()}}, "cwd": rp.as_posix()}, "deny"),
            ("write_director", {"toolCall": {"name": "write_file", "args": {"Path": (dir_dir / "RESUME_STATE.md").as_posix()}}, "cwd": rp.as_posix()}, "allow"),
            ("branch", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git switch -c dw-gate-probe"}}, "cwd": rp.as_posix()}, "deny"),
        ]
        for cname, payload, expected in cases:
            proc = _run_shell_hook(cmd_test, rp / ".agents", payload)
            if isinstance(proc, Exception):
                return check_item("workspace_spec_gate_exec", "FAIL", f"{cname}: {proc}")
            try:
                got = json.loads(proc.stdout.strip()).get("decision")
            except ValueError:
                got = None
            if proc.returncode != 0 or got != expected:
                return check_item("workspace_spec_gate_exec", "FAIL", f"{cname}: expected {expected!r}, got {got!r} rc={proc.returncode} {proc.stderr.strip()[:120]}")
    return check_item("workspace_spec_gate_exec", "HOOK_EXEC_OK", "passed 4/4 (read=allow, product=deny, DIRECTOR=allow, branch=deny)")


def _extract_pretool_command(hooks_obj, preferred_group=None):
    if not isinstance(hooks_obj, dict):
        return None
    groups = [preferred_group] if (preferred_group and preferred_group in hooks_obj) else list(hooks_obj.keys())
    for gname in groups:
        grp = hooks_obj.get(gname)
        if not isinstance(grp, dict):
            continue
        for entry in grp.get("PreToolUse") or []:
            if isinstance(entry, dict):
                for hk in entry.get("hooks") or []:
                    if isinstance(hk, dict) and isinstance(hk.get("command"), str) and hk["command"].strip():
                        return hk["command"].strip()
    return None


def validate_plugin_manifest(mdata):
    if not isinstance(mdata, dict):
        return ["plugin.json root must be a JSON object"]
    errs = []
    extra = sorted(set(mdata) - ALLOWED_PLUGIN_FIELDS)
    if extra:
        errs.append(f"unknown keys: {extra}")
    name = mdata.get("name")
    if not isinstance(name, str) or not PLUGIN_NAME_RE.fullmatch(name):
        errs.append("invalid name")
    for sk in ("$schema", "displayName", "display_name", "description", "version", "homepage", "repository", "license", "logo", "source", "sourcePath", "source_path"):
        if sk in mdata and not isinstance(mdata[sk], str):
            errs.append(f"{sk} must be string")
    for pk in ("suggestedPrompts", "suggested_prompts"):
        if pk in mdata:
            val = mdata[pk]
            if not isinstance(val, list) or len(val) > 3 or not all(isinstance(x, str) and NON_WS_RE.search(x) for x in val):
                errs.append(f"{pk} must be array of <=3 strings")
    return errs


def check_plugin_dir(plugin_dir):
    items = []
    pdir = pathlib.Path(plugin_dir)
    manifest = pdir / "plugin.json"
    if not manifest.is_file():
        return [check_item("plugin", "FAIL", f"missing {manifest.as_posix()}")]
    try:
        mdata = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [check_item("plugin", "FAIL", f"invalid {manifest.name}: {exc}")]
    m_errs = validate_plugin_manifest(mdata)
    items.append(check_item("plugin_manifest", "FAIL" if m_errs else "OK", "; ".join(m_errs) if m_errs else f"plugin `{mdata['name']}` schema-valid"))

    phooks = pdir / "hooks.json"
    if phooks.is_file():
        try:
            ph_obj = json.loads(phooks.read_text(encoding="utf-8-sig"))
            pcmd = _extract_pretool_command(ph_obj)
            if not pcmd:
                items.append(check_item("plugin_hooks_exec", "FAIL", "missing PreToolUse command"))
            else:
                m_mode = re.search(r"--mode\s+(remind|ask|block)\b", pcmd)
                items.append(_probe_pretool_command("plugin_hooks_exec", pcmd, pdir, mode=(m_mode.group(1) if m_mode else "remind")))
        except (OSError, ValueError) as exc:
            items.append(check_item("plugin_hooks_exec", "FAIL", f"invalid hooks.json: {exc}"))

    rules_file = pdir / "rules" / "AGENTS.md"
    if rules_file.is_file():
        sz = rules_file.stat().st_size
        items.append(check_item("plugin_rules", "OK" if sz <= 24000 else "FAIL", f"{rules_file.name}: {sz} bytes (limit 24,000)"))

    for skill in sorted((pdir / "skills").glob("*/SKILL.md")) if (pdir / "skills").is_dir() else []:
        txt = skill.read_text(encoding="utf-8-sig", errors="replace")
        fm = re.match(r"^---\r?\n(.*?)\r?\n---", txt, re.S)
        m_name = re.search(r"^name:\s*(\S+)", fm.group(1), re.M) if fm else None
        m_desc = re.search(r"^description:\s*\S+", fm.group(1), re.M) if fm else None
        ok_sk = bool(fm and m_name and m_name.group(1) == skill.parent.name and m_desc)
        items.append(check_item("plugin_skill", "OK" if ok_sk else "FAIL", f"skill `{skill.parent.name}`"))

    for agent in sorted((pdir / "agents").glob("*.md")) if (pdir / "agents").is_dir() else []:
        txt = agent.read_text(encoding="utf-8-sig", errors="replace")
        fm = re.match(r"^---\r?\n(.*?)\r?\n---", txt, re.S)
        if not fm:
            items.append(check_item("plugin_agent", "FAIL", f"{agent.name}: missing frontmatter"))
            continue
        m_tools = re.search(r"^tools:\s*\[([^\]]*)\]", fm.group(1), re.M)
        tools_list = [t.strip().strip("'\"") for t in m_tools.group(1).split(",") if t.strip()] if m_tools else []
        if any(t in HANG_RISK_AGENT_TOOLS for t in tools_list):
            items.append(check_item("plugin_agent", "FAIL", f"{agent.name}: unmapped App 2.0 tool(s) in {tools_list}"))
            continue
        if agent.name == "dw-blind-reviewer.md":
            imcp = re.search(r"^inheritMcp:\s*false\b", fm.group(1), re.M)
            icust = re.search(r"^inheritCustomizations:\s*false\b", fm.group(1), re.M)
            if tools_list != ["read_file"] or not imcp or not icust:
                items.append(check_item("plugin_agent", "FAIL", f"{agent.name}: requires tools:[read_file], inheritMcp:false, inheritCustomizations:false"))
                continue
        items.append(check_item("plugin_agent", "OK", f"agent `{agent.stem}` valid"))
    return items


def check_hooks_and_probe(repo, probe_hooks, beginner=False):
    items = []
    ran_workspace_exec = False
    if repo:
        rp = pathlib.Path(repo)
        hooks_path = rp / ".agents" / "hooks.json"
        if hooks_path.is_file():
            try:
                data = json.loads(hooks_path.read_text(encoding="utf-8-sig"))
                sc = _COMMON.load_sibling(HERE, "agy_setup_check", prefix="_agy_doc_")
                h_probs, pre_cmd, sg_cmd = sc.validate_hooks_structure(data, beginner=beginner)
                if h_probs:
                    items.append(check_item("workspace_hooks_config", "FAIL", "; ".join(h_probs)))
                    items.append(check_item("workspace_hook_exec", "NOT_ENFORCED", "skipped due to hook config problems"))
                    ran_workspace_exec = True
                else:
                    items.append(check_item("workspace_hooks_config", "CONFIGURED", f"{hooks_path.as_posix()} valid ({len(data)} group(s))"))
                    if sg_cmd:
                        items.append(_probe_spec_gate_command(sg_cmd, rp))
                    if pre_cmd:
                        m_mode = re.search(r"--mode\s+(remind|ask|block)\b", pre_cmd)
                        hard_only = bool(re.search(r"(?:^|\s)--hard-only(?:\s|$)", pre_cmd))
                        items.append(_probe_pretool_command("workspace_hook_exec", pre_cmd, hooks_path.parent, mode=(m_mode.group(1) if m_mode else "remind"), hard_only=hard_only))
                    items.append(check_item("live_host_hook_dispatch", "CONFIGURED_NOT_VERIFIED", "command probes succeeded locally; host UI event firing requires runtime audit verification"))
                    ran_workspace_exec = True
            except (OSError, ValueError) as exc:
                items.append(check_item("workspace_hooks_config", "FAIL", f"{hooks_path.as_posix()} invalid JSON: {exc}"))
        else:
            items.append(check_item("workspace_hooks_config", "MISSING", f"{hooks_path.as_posix()} not found"))

        plug_cfg = rp / ".agents" / "plugins.json"
        if plug_cfg.is_file():
            try:
                pdata = json.loads(plug_cfg.read_text(encoding="utf-8-sig"))
                entries = pdata.get("entries") if isinstance(pdata, dict) else None
                matched = None
                if isinstance(entries, list):
                    for ent in entries:
                        if isinstance(ent, dict) and isinstance(ent.get("path"), str):
                            ep = pathlib.Path(ent["path"])
                            ep = (rp / ep).resolve() if not ep.is_absolute() else ep
                            if (ep / "director-work" / "plugin.json").is_file() or (ep.name == "director-work" and (ep / "plugin.json").is_file()):
                                matched = ep
                                break
                items.append(check_item("workspace_plugins_config", "CONFIGURED" if matched else "FAIL", f"{plug_cfg.as_posix()} {'-> ' + matched.as_posix() if matched else 'missing director-work entry'}"))
            except (OSError, ValueError) as exc:
                items.append(check_item("workspace_plugins_config", "FAIL", f"invalid JSON: {exc}"))
        else:
            items.append(check_item("workspace_plugins_config", "MISSING", f"{plug_cfg.as_posix()} not found"))

    if probe_hooks and not ran_workspace_exec:
        guard, rules = HERE / "agy_guard.py", _COMMON.find_base_tools(HERE) / "guard_rules.json"
        if not guard.is_file() or not rules.is_file():
            items.append(check_item("guard_probe", "FAIL", "agy_guard.py or tools/guard_rules.json missing"))
        else:
            py_q = f'"{sys.executable}"' if re.search(r"\s", sys.executable) else sys.executable
            if os.name == "nt" and py_q.startswith('"'):
                py_q = f"call {py_q}"
            g_q = f'"{guard.as_posix()}"' if re.search(r"\s", guard.as_posix()) else guard.as_posix()
            r_q = f'"{rules.as_posix()}"' if re.search(r"\s", rules.as_posix()) else rules.as_posix()
            items.append(_probe_pretool_command("guard_probe", f"{py_q} -B -I -X utf8 {g_q} --rules {r_q} --profile worker --mode block", HERE, mode="block"))
    return items


def check_licenses(path_str, warn_days):
    p = pathlib.Path(path_str)
    if not p.is_file():
        return [check_item("licenses", "FAIL", f"missing: {p}")], 0, 0, 0, 1
    try:
        raw = p.read_text(encoding="utf-8-sig")
        entries = (json.loads(raw).get("licenses", []) if isinstance(json.loads(raw), dict) else json.loads(raw)) if p.suffix.lower() == ".json" else [
            {"tool": c[0], "license": c[1], "expires": c[2]}
            for l in raw.splitlines()
            if l.strip().startswith("|") and "---" not in l
            for c in [[x.strip() for x in l.strip().strip("|").split("|")]]
            if len(c) >= 3 and c[0].lower() != "tool"
        ]
    except (OSError, ValueError) as exc:
        return [check_item("licenses", "FAIL", f"cannot parse {p}: {exc}")], 0, 0, 0, 1
    today, items = datetime.date.today(), []
    ok = exp_i = exp_d = bad = 0
    for e in entries:
        try:
            left = (datetime.date.fromisoformat(str(e.get("expires") or "")) - today).days
        except ValueError:
            bad += 1
            items.append(check_item("licenses", "WARN", f"bad expiry for {e.get('license')}"))
            continue
        if left < 0:
            exp_d += 1
            items.append(check_item("licenses", "WARN", f"expired: {e.get('license')}"))
        elif left <= warn_days:
            exp_i += 1
            items.append(check_item("licenses", "WARN", f"expiring: {e.get('license')} ({left}d)"))
        else:
            ok += 1
            items.append(check_item("licenses", "OK", f"{e.get('license')}: {left}d"))
    return items, ok, exp_i, exp_d, bad


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo")
    ap.add_argument("--plugin")
    ap.add_argument("--config")
    ap.add_argument("--brain-root", action="append", default=[])
    ap.add_argument("--beginner", action="store_true")
    ap.add_argument("--probe-hooks", action="store_true")
    ap.add_argument("--licenses")
    ap.add_argument("--license-days", type=int, default=3)
    ap.add_argument("--licenses-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    if a.licenses_only:
        if not a.licenses:
            return 2
        litems, ok, ei, ed, bad = check_licenses(a.licenses, a.license_days)
        for it in litems:
            print(f"{it['status']:8s} {it['name']}: {it['detail']}")
        print(f"LICENSES: ok={ok} expiring={ei} expired={ed} bad={bad}")
        return 1 if (ei or ed or bad) else 0

    items = []
    pv = sys.version_info
    items.append(check_item("python", "OK" if pv >= (3, 9) else "FAIL", f"Python {pv.major}.{pv.minor}.{pv.micro}"))
    git_bin = shutil.which("git")
    items.append(check_item("git", "OK" if git_bin else "FAIL", git_bin or "git missing"))
    items.append(check_os_sandbox())

    agy_bin = shutil.which("agy")
    items.append(check_item("agy_cli", "OK" if agy_bin else "INFO", agy_bin or "agy CLI not on PATH (normal inside App)"))

    base_tools = _COMMON.find_base_tools(HERE)
    miss_base = [t for t in REQUIRED_BASE_TOOLS if not (base_tools / t).is_file()]
    miss_ad = [t for t in REQUIRED_ADAPTER_FILES if not (HERE / t).is_file()]
    items.append(check_item("tools", "FAIL" if miss_base else "OK", f"missing base tools: {miss_base}" if miss_base else f"all {len(REQUIRED_BASE_TOOLS)} base tools in {base_tools.as_posix()}"))
    items.append(check_item("adapter", "FAIL" if miss_ad else "OK", f"missing adapter files: {miss_ad}" if miss_ad else f"all {len(REQUIRED_ADAPTER_FILES)} adapter files present"))

    items += check_hooks_and_probe(a.repo, a.probe_hooks, beginner=a.beginner)

    pdir = pathlib.Path(a.plugin) if a.plugin else (HERE / "plugin" / "director-work")
    if pdir.is_dir():
        items += check_plugin_dir(pdir)

    if a.repo:
        rpath = pathlib.Path(a.repo)
        for rp_file in (rpath / "AGENTS.md", rpath / "GEMINI.md", rpath / ".agent" / "rules" / "director-work.md"):
            if rp_file.is_file():
                sz = rp_file.stat().st_size
                items.append(check_item("project_rules", "OK" if sz <= 24000 else "FAIL", f"{rp_file.relative_to(rpath).as_posix()}: {sz} bytes"))

    if a.config:
        cpath = pathlib.Path(a.config)
        try:
            cfg = json.loads(cpath.read_text(encoding="utf-8-sig"))
            items.append(check_item("config", "OK" if isinstance(cfg, dict) and cfg.get("schema_version") == 1 else "FAIL", f"{cpath.as_posix()}"))
        except (OSError, ValueError) as exc:
            items.append(check_item("config", "FAIL", f"{cpath.as_posix()}: {exc}"))

    if a.licenses:
        items += check_licenses(a.licenses, a.license_days)[0]

    counts = {s: sum(1 for it in items if it["status"] == s)
              for s in ("OK", "HOOK_EXEC_OK", "CONFIGURED", "CONFIGURED_NOT_VERIFIED", "NOT_ENFORCED", "MISSING", "WARN", "FAIL", "INFO")}
    summary = {"status": "FAIL" if counts["FAIL"] else "OK", "counts": counts, "checks": items}
    if a.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for it in items:
            print(f"{it['status']:24s} {it['name']}: {it['detail']}")
        ok_t = counts["OK"] + counts["HOOK_EXEC_OK"] + counts["CONFIGURED"]
        warn_t = counts["WARN"] + counts["NOT_ENFORCED"] + counts["MISSING"]
        print(f"DOCTOR_AGY: ok={ok_t} warn={warn_t} not_verified={counts['CONFIGURED_NOT_VERIFIED']} fail={counts['FAIL']}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
