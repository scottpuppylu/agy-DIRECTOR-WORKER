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
import hashlib
import importlib.util
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


def _run_shell_hook(cmd_line, cwd_dir, payload, extra_env=None):
    """Invoke a configured hook command identically to Antigravity on the host OS."""
    inp = json.dumps(payload, ensure_ascii=False)
    env = dict(os.environ, **(extra_env or {}))
    try:
        if os.name == "nt":
            return subprocess.run(
                "cmd /d /c " + cmd_line,
                cwd=str(cwd_dir),
                input=inp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=20,
            )
        return subprocess.run(
            ["sh", "-c", cmd_line],
            cwd=str(cwd_dir),
            input=inp,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return exc


def _quote_probe_path(p):
    s = pathlib.Path(p).resolve().as_posix()
    if re.fullmatch(r"[A-Za-z0-9_./:{}-]+", s):
        return s
    return f'"{s}"' if os.name == "nt" else shlex.quote(s)


def _sanitize_probe_cmd(cmd_line, probe_dir):
    """Redirect stateful guard flags (--audit/--bridge-state/--pause-flag) into disposable temp storage."""
    repl = {
        "--audit": _quote_probe_path(probe_dir / "probe_audit.jsonl"),
        "--bridge-state": _quote_probe_path(probe_dir / "probe_bridge"),
        "--pause-flag": _quote_probe_path(probe_dir / "probe_pause.flag"),
    }

    def _sub(m):
        opt = m.group(1).lower()
        sep = m.group(2)
        return f"{m.group(1)}{sep}{repl[opt]}"

    return re.sub(r'(--(?:audit|bridge-state|pause-flag))(=|\s+)(?:"[^"]*"|\'[^\']*\'|\S+)', _sub, cmd_line, flags=re.I)


def _probe_pretool_command(check_name, cmd_line, cwd_dir, mode="remind"):
    """Live-execute an extracted PreToolUse hook command in read-only probe mode against deny/allow/G01."""
    cases = [
        ("deny_env_read", {"toolCall": {"name": "read_file", "args": {"Path": ".env"}}, "hookEventName": "PreToolUse"}, "deny", None),
        ("allow_status", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git status"}}, "hookEventName": "PreToolUse"}, "allow", None),
    ]
    g01_expected = "deny" if mode == "block" else ("force_ask" if mode == "ask" else "allow")
    g01_substr = "[G01]" if mode != "block" else None
    cases.append(
        ("g01_force_push", {"toolCall": {"name": "run_command", "args": {"CommandLine": "git push --force origin main"}}, "hookEventName": "PreToolUse"}, g01_expected, g01_substr)
    )
    with tempfile.TemporaryDirectory(prefix="agydw_doctor_probe_") as td:
        probe_cmd = _sanitize_probe_cmd(cmd_line, pathlib.Path(td))
        probe_env = {"AGYDW_DOCTOR_PROBE": "1"}
        for cname, payload, expected_dec, want_substr in cases:
            proc = _run_shell_hook(probe_cmd, cwd_dir, payload, extra_env=probe_env)
            if isinstance(proc, Exception):
                return check_item(check_name, "FAIL", f"{cname}: hook execution failed: {proc}")
            try:
                res = json.loads(proc.stdout.strip())
                got = res.get("decision")
                reason = str(res.get("reason") or "")
            except ValueError:
                got, reason = None, ""
            if proc.returncode != 0 or got != expected_dec or (want_substr and want_substr not in reason):
                return check_item(
                    check_name,
                    "FAIL",
                    f"{cname}: expected decision={expected_dec!r}" + (f" reason~={want_substr!r}" if want_substr else "")
                    + f" rc=0, got decision={got!r} reason={reason!r} rc={proc.returncode} stderr={proc.stderr.strip()[:120]}",
                )
    return check_item(
        check_name,
        "HOOK_EXEC_OK",
        f"executed `{cmd_line[:60]}{'...' if len(cmd_line) > 60 else ''}` passed 3/3 (.env=deny, git status=allow, force-push {mode}={g01_expected}; live runtime firing verified separately via audit log)",
    )


def _extract_pretool_command(hooks_obj, preferred_group=None):
    """Return first PreToolUse hook command string from an Antigravity hooks.json dict."""
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


def _validate_hooks_dict(data):
    """Share strict hook structure checks with agy_setup_check.py."""
    sc_path = HERE / "agy_setup_check.py"
    if sc_path.is_file():
        spec = importlib.util.spec_from_file_location("_agy_doc_setup_check", sc_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.validate_hooks_structure(data)
    if not isinstance(data, dict):
        return ["hooks config must be a JSON object"], None
    return [], _extract_pretool_command(data, "agydw-guard")


def _check_active_tools_version(cmd_line):
    """Verify active agy_guard.py copy against AGYDW_TOOLS_MANIFEST.json and current VERSION."""
    if not cmd_line:
        return None
    m = re.search(r'"([^"]*?agy_guard\.py)"|\'([^\']*?agy_guard\.py)\'|([^\s"\']*agy_guard\.py)', cmd_line)
    if not m:
        return None
    guard_path = pathlib.Path(next(g for g in m.groups() if g))
    if not guard_path.is_file():
        return check_item("active_tools_version", "FAIL", f"configured agy_guard.py missing: {guard_path.as_posix()}")
    tools_dir = guard_path.parent
    manifest_p = tools_dir / "AGYDW_TOOLS_MANIFEST.json"
    vf = HERE.parent / "VERSION"
    repo_ver = vf.read_text(encoding="utf-8-sig", errors="replace").strip() if vf.is_file() else None
    repo_guard = HERE / "agy_guard.py"
    cur_guard_sha = hashlib.sha256(guard_path.read_bytes()).hexdigest()

    if tools_dir.resolve() == HERE.resolve():
        return check_item("active_tools_version", "OK", f"running directly from rules tools/ (version={repo_ver or 'source'}, sha256={cur_guard_sha[:12]})")

    if not manifest_p.is_file():
        return check_item("active_tools_version", "WARN", f"unmanifested/legacy tools copy at {tools_dir.as_posix()} (sha256={cur_guard_sha[:12]}; run agy_setup.py --apply to upgrade)")
    try:
        mdata = json.loads(manifest_p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return check_item("active_tools_version", "WARN", f"corrupt {manifest_p.name}: {exc}")

    inst_ver = str(mdata.get("version") or "unknown")
    want_sha = (mdata.get("files") or {}).get("agy_guard.py")
    if want_sha and want_sha != cur_guard_sha:
        return check_item("active_tools_version", "WARN", f"tools copy at {tools_dir.as_posix()} modified after install (manifest {inst_ver})")
    if repo_ver and inst_ver != repo_ver:
        return check_item("active_tools_version", "WARN", f"active tools version {inst_ver} differs from rules repo {repo_ver}; re-run agy_setup.py --apply")
    if repo_guard.is_file():
        repo_sha = hashlib.sha256(repo_guard.read_bytes()).hexdigest()
        if repo_sha != cur_guard_sha:
            return check_item("active_tools_version", "WARN", f"active agy_guard.py differs from {HERE.as_posix()}; re-run agy_setup.py --apply")
    mode_lbl = "versioned" if tools_dir.name.startswith("tools_v") else "compat"
    return check_item("active_tools_version", "OK", f"active {mode_lbl} tools version={inst_ver} at {tools_dir.as_posix()} (sha256={cur_guard_sha[:12]})")


def check_hooks_and_probe(repo, probe_hooks):
    """Validate .agents/hooks.json, .agents/plugins.json, and live-execute configured hook commands."""
    items = []
    ran_workspace_exec = False
    if repo:
        rp = pathlib.Path(repo)
        hooks_path = rp / ".agents" / "hooks.json"
        if hooks_path.is_file():
            try:
                data = json.loads(hooks_path.read_text(encoding="utf-8-sig"))
                h_probs, cmd_line = _validate_hooks_dict(data)
                if h_probs:
                    items.append(check_item("workspace_hooks_config", "FAIL", "; ".join(h_probs)))
                    items.append(check_item("workspace_hook_exec", "NOT_ENFORCED", "skipped/not enforced because workspace hook config has failing lifecycle/matcher/enabled rules"))
                    ran_workspace_exec = True
                else:
                    items.append(check_item("workspace_hooks_config", "CONFIGURED", f"{hooks_path.as_posix()} configured & enabled across core PreToolUse matchers and all 4 lifecycle events ({len(data)} group(s))"))
                    m_mode = re.search(r"--mode\s+(remind|ask|block)\b", cmd_line or "")
                    mode = m_mode.group(1) if m_mode else "remind"
                    items.append(_probe_pretool_command("workspace_hook_exec", cmd_line, hooks_path.parent, mode=mode))
                    ver_item = _check_active_tools_version(cmd_line)
                    if ver_item:
                        items.append(ver_item)
                    items.append(check_item("live_host_hook_dispatch", "CONFIGURED_NOT_VERIFIED", "command probes succeeded locally; host App/IDE/CLI event firing requires runtime audit verification"))
                    ran_workspace_exec = True
            except (OSError, ValueError) as exc:
                items.append(check_item("workspace_hooks_config", "FAIL", f"{hooks_path.as_posix()} invalid JSON: {exc}"))
        else:
            items.append(check_item("workspace_hooks_config", "MISSING", f"{hooks_path.as_posix()} not found (run agy_setup.py --apply)"))

        plug_cfg = rp / ".agents" / "plugins.json"
        if plug_cfg.is_file():
            try:
                pdata = json.loads(plug_cfg.read_text(encoding="utf-8-sig"))
                entries = pdata.get("entries") if isinstance(pdata, dict) else None
                if not isinstance(entries, list):
                    items.append(check_item("workspace_plugins_config", "FAIL", f"{plug_cfg.as_posix()} missing 'entries' list"))
                else:
                    matched = None
                    for ent in entries:
                        if isinstance(ent, dict) and isinstance(ent.get("path"), str):
                            ep = pathlib.Path(ent["path"])
                            if not ep.is_absolute():
                                ep = (rp / ep).resolve()
                            if (ep / "director-work" / "plugin.json").is_file() or (ep.name == "director-work" and (ep / "plugin.json").is_file()):
                                matched = ep
                                break
                    if matched:
                        items.append(check_item("workspace_plugins_config", "CONFIGURED", f"{plug_cfg.as_posix()} registers director-work ({matched.as_posix()})"))
                    else:
                        items.append(check_item("workspace_plugins_config", "FAIL", f"{plug_cfg.as_posix()} has {len(entries)} entry/entries but none registers director-work (run agy_setup.py --apply)"))
            except (OSError, ValueError) as exc:
                items.append(check_item("workspace_plugins_config", "FAIL", f"{plug_cfg.as_posix()} invalid JSON: {exc}"))
        else:
            items.append(check_item("workspace_plugins_config", "MISSING", f"{plug_cfg.as_posix()} not found (run agy_setup.py --apply)"))

    if probe_hooks and not ran_workspace_exec:
        guard = HERE / "agy_guard.py"
        rules = HERE / "guard_rules.json"
        if not guard.is_file() or not rules.is_file():
            items.append(check_item("guard_probe", "FAIL", "agy_guard.py or guard_rules.json missing"))
            return items
        py_q = f'"{sys.executable}"' if re.search(r"\s", sys.executable) else sys.executable
        if os.name == "nt" and py_q.startswith('"'):
            py_q = f"call {py_q}"
        g_q = f'"{guard.as_posix()}"' if re.search(r"\s", guard.as_posix()) else guard.as_posix()
        r_q = f'"{rules.as_posix()}"' if re.search(r"\s", rules.as_posix()) else rules.as_posix()
        cmd = f"{py_q} -B -I -X utf8 {g_q} --rules {r_q} --profile worker --mode block"
        items.append(_probe_pretool_command("guard_probe", cmd, HERE, mode="block"))
    return items


def validate_plugin_manifest(mdata):
    """Validate plugin.json against https://antigravity.google/schemas/v1/plugin.json types."""
    if not isinstance(mdata, dict):
        return ["plugin.json root must be a JSON object"]
    errs = []
    extra = sorted(set(mdata) - ALLOWED_PLUGIN_FIELDS)
    if extra:
        errs.append(f"unknown keys dropped by loader: {extra}")
    name = mdata.get("name")
    if not isinstance(name, str) or not PLUGIN_NAME_RE.fullmatch(name):
        errs.append("name must be a string matching ^[a-zA-Z0-9_-]+$")
    for sk in ("$schema", "displayName", "display_name", "description", "version", "homepage", "repository", "license", "logo", "source", "sourcePath", "source_path"):
        if sk in mdata and not isinstance(mdata[sk], str):
            errs.append(f"{sk} must be string (got {type(mdata[sk]).__name__})")
    if "disabled" in mdata and not isinstance(mdata["disabled"], bool):
        errs.append(f"disabled must be boolean (got {type(mdata['disabled']).__name__})")
    for pk in ("suggestedPrompts", "suggested_prompts"):
        if pk in mdata:
            val = mdata[pk]
            if not isinstance(val, list) or len(val) > 3 or not all(isinstance(x, str) and NON_WS_RE.search(x) for x in val):
                errs.append(f"{pk} must be array of <=3 non-blank strings")
    if "skills" in mdata:
        sv = mdata["skills"]
        ok_s = (isinstance(sv, str) and bool(NON_WS_RE.search(sv))) or (
            isinstance(sv, list) and all(isinstance(x, str) and bool(NON_WS_RE.search(x)) for x in sv)
        )
        if not ok_s:
            errs.append("skills must be non-blank string or array of non-blank strings")
    if "author" in mdata:
        av = mdata["author"]
        if isinstance(av, dict):
            extra_a = sorted(set(av) - {"name", "email", "url"})
            if extra_a:
                errs.append(f"author object has unknown keys: {extra_a}")
            for ak, val in av.items():
                if not isinstance(val, str):
                    errs.append(f"author.{ak} must be string")
        elif not isinstance(av, str):
            errs.append(f"author must be string or object (got {type(av).__name__})")
    if "keywords" in mdata:
        kv = mdata["keywords"]
        if not isinstance(kv, list) or not all(isinstance(x, str) for x in kv):
            errs.append("keywords must be array of strings")
    if "extensions" in mdata:
        ev = mdata["extensions"]
        if not isinstance(ev, dict) or not all(isinstance(v, dict) for v in ev.values()):
            errs.append("extensions must be object of objects")
    return errs


def check_plugin_dir(plugin_dir):
    """Validate an Antigravity plugin folder (manifest types, hooks exec, AGENTS.md <= 24KB, skills, agents)."""
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
    if m_errs:
        items.append(check_item("plugin_manifest", "FAIL", "; ".join(m_errs)))
    else:
        items.append(check_item("plugin_manifest", "OK", f"plugin `{mdata['name']}` schema-valid ({len(mdata.get('suggestedPrompts') or [])} starter prompts)"))

    phooks = pdir / "hooks.json"
    if phooks.is_file():
        try:
            ph_obj = json.loads(phooks.read_text(encoding="utf-8-sig"))
            pcmd = _extract_pretool_command(ph_obj)
            if not pcmd:
                items.append(check_item("plugin_hooks_exec", "FAIL", f"{phooks.name}: missing PreToolUse command"))
            else:
                m_mode = re.search(r"--mode\s+(remind|ask|block)\b", pcmd)
                items.append(_probe_pretool_command("plugin_hooks_exec", pcmd, pdir, mode=(m_mode.group(1) if m_mode else "remind")))
        except (OSError, ValueError) as exc:
            items.append(check_item("plugin_hooks_exec", "FAIL", f"{phooks.name}: invalid JSON: {exc}"))

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
        tools_list = [t.strip().strip("'\"") for t in m_tools.group(1).split(",") if t.strip()] if m_tools else []
        if m_tools:
            risky = [t for t in tools_list if t in HANG_RISK_AGENT_TOOLS]
            if risky:
                items.append(check_item("plugin_agent", "FAIL", f"{agent.name}: unmapped App 2.0 tool(s) {risky} risk subagent hang"))
                continue
        if agent.name == "dw-blind-reviewer.md":
            imcp = re.search(r"^inheritMcp:\s*false\b", fm.group(1), re.M)
            icust = re.search(r"^inheritCustomizations:\s*false\b", fm.group(1), re.M)
            if tools_list != ["read_file"] or not imcp or not icust:
                items.append(check_item("plugin_agent", "FAIL", f"{agent.name}: blind reviewer requires tools: [read_file], inheritMcp: false, inheritCustomizations: false"))
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
    if hasattr(sys.stdout, "reconfigure"):
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
    py_note = ""
    if os.name != "nt" and not shutil.which("python") and shutil.which("python3"):
        py_note = " (PATH has python3 only; plugin seatbelt uses python || python3 fallback)"
    items.append(check_item("python", "OK" if pv >= (3, 9) else "FAIL", f"Python {pv.major}.{pv.minor}.{pv.micro}{py_note}"))
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
              for s in ("OK", "HOOK_EXEC_OK", "CONFIGURED", "CONFIGURED_NOT_VERIFIED", "NOT_ENFORCED", "MISSING", "WARN", "FAIL", "INFO")}
    summary = {"status": "FAIL" if counts["FAIL"] else "OK", "counts": counts, "checks": items}

    if a.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for it in items:
            print(f"{it['status']:24s} {it['name']}: {it['detail']}")
        ok_total = counts["OK"] + counts["HOOK_EXEC_OK"] + counts["CONFIGURED"]
        warn_total = counts["WARN"] + counts["NOT_ENFORCED"] + counts["MISSING"]
        print(f"DOCTOR_AGY: ok={ok_total} warn={warn_total} not_verified={counts['CONFIGURED_NOT_VERIFIED']} fail={counts['FAIL']}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
