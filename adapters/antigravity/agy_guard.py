#!/usr/bin/env python3
"""Antigravity PreToolUse seat-belt guard (3.0.0-agy.7). Fail-closed.

Maps Antigravity native tool calls (`run_command`, `read_file`, `view_file`,
`find_by_name`, `grep_search`, `write_file`, `write_to_file`, `edit_file`,
`replace_file_content`, `multi_replace_file_content`, `send_message`,
`call_mcp_tool`, `mcp_*`) onto frozen `tools/hook_guard.py` + `tools/guard_rules.json`
and enforces Antigravity-specific G43 (config/rules/hooks/plugins) & G53 (OAuth creds)
plus `--protect` governance locks over `DIRECTOR/` files.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agy_guard_common", HERE / "agy_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()

AGY_CONFIG_GLOBS = [
    "**/.agents/hooks*.json",
    "**/.agents/plugins.json",
    "**/.agent/rules/**",
    "**/plugin/director-work/hooks.json",
    "**/seatbelt_hook.py",
    "~/.gemini/antigravity/**",
    "~/.gemini/config/**",
]
AGY_OAUTH_GLOBS = [
    "~/.gemini/oauth_creds.json",
    "~/.gemini/**/credentials*",
    "**/.gemini/oauth_creds.json",
    "**/.gemini/**/oauth_creds*",
    "**/.gemini/**/credentials*",
    "**/oauth_creds.json",
]
SHELL_OAUTH_READ_CMDS = {
    "cat", "tac", "nl", "more", "less", "bat", "head", "tail", "sed", "awk", "gawk",
    "grep", "egrep", "fgrep", "rg", "findstr", "select-string", "sls", "get-content",
    "gc", "type", "cp", "copy", "copy-item", "cpi", "mv", "move", "move-item", "mi",
    "scp", "rsync", "base64", "certutil", "xxd", "od", "hexdump", "strings",
    "format-hex", "fhx", "import-csv", "get-filehash", "curl", "wget",
    "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "tar", "zip", "gzip",
    "jq", "yq", "tr", "cut", "sort", "uniq", "wc", "diff", "cmp", "comm", "paste",
    "tee", "dd", "openssl", "python", "python3", "py", "node", "perl", "ruby",
    "pbcopy", "clip", "xclip", "xsel", "set-clipboard", "scb",
}
SHELL_SCRIPT_INTERPRETERS = {"python", "python3", "py", "node", "perl", "ruby"}
OAUTH_TEXT_RE = re.compile(r"(?:oauth_creds\.json|(?:^|[/\\~%$])\.gemini[/\\].*(?:oauth|credentials))", re.I)
INPUT_REDIR_RE = re.compile(r"(?:^|[\s;|&(])0?<\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|<>]+))")
PS_INFILE_ARG_RE = re.compile(r"^-infile:(.+)$", re.I)
DIRECTOR_PROTECTED_REL = (
    "DIRECTOR_LOG.md",
    "DIRECTOR_STATE.md",
    "PO_PENDING.md",
    "director_work.json",
    ".sessions.json",
    ".agydw_setup_state.json",
    "orders/**",
    "tools/**",
    "adapters/**",
    "brief-history/**",
)
PS_PATH_ARG_RE = re.compile(r"^-(?:path|literalpath|filepath):(.+)$", re.I)
SAFE_AGY_FAST_READS = {"quick_outline", "batch_search", "read_slices"}


def _emit(fmt, decision, reason="", overwrite=None):
    """Emit Antigravity JSON decision (`allow|deny|force_ask`, rc=0) or Claude Code protocol."""
    if fmt == "claude":
        if decision == "deny":
            print(reason or "BLOCKED by agy_guard", file=sys.stderr)
            return 2
        if decision == "ask":
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }, ensure_ascii=False))
            return 0
        if reason:
            print(json.dumps({
                "systemMessage": reason,
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": reason},
            }, ensure_ascii=False))
        return 0

    agy_dec = "force_ask" if decision == "ask" else decision
    out = {"decision": agy_dec}
    if reason:
        out["reason"] = reason
    if overwrite and agy_dec == "allow":
        out["overwrite"] = overwrite
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _inject_utf8_flag(cmd):
    """Inject `-X utf8` right after bare `python` invocations without adding `-I`."""
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    m = re.match(
        r'^(\s*(?:call\s+)?(?:"[^"]*?python(?:\d+(?:\.\d+)?)?(?:\.exe)?"|\'[^\']*?python(?:\d+(?:\.\d+)?)?(?:\.exe)?\'|python(?:\d+(?:\.\d+)?)?(?:\.exe)?|py))\b(.*)$',
        cmd,
        re.I | re.S,
    )
    if not m:
        return None
    head, rest = m.group(1), m.group(2)
    if re.search(r"(?:^|\s)-X\s*utf8\b", rest):
        return None
    return f"{head} -X utf8{rest}"


def _extract_mcp_virtual_name_and_args(tool, args):
    if tool == "call_mcp_tool":
        srv = str(args.get("ServerName") or args.get("server") or "unknown").strip()
        act = str(args.get("ToolName") or args.get("tool") or "unknown").strip()
        inner = args.get("Arguments")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except ValueError:
                inner = {}
        return f"mcp__{srv}__{act}", act.lower(), (inner if isinstance(inner, dict) else {})
    if tool.startswith("mcp__"):
        act = tool.split("__")[-1].lower()
        return tool, act, args
    if tool.startswith("mcp_"):
        body = tool[4:]
        for marker in ("_fast_", "__"):
            if marker in body:
                srv, act = body.split(marker, 1)
                return f"mcp__{srv}__{act}", act.lower(), args
        parts = body.split("_", 1)
        if len(parts) == 2:
            return f"mcp__{parts[0]}__{parts[1]}", parts[1].lower(), args
        return f"mcp__agy__{body}", body.lower(), args
    return tool, "", args


def _normalize_call(raw_payload):
    """Normalize Antigravity `toolCall` or Claude `tool_name` payload to hook_guard schema."""
    tc = raw_payload.get("toolCall") if isinstance(raw_payload.get("toolCall"), dict) else None
    is_agy = tc is not None or "hookEventName" in raw_payload
    if tc is not None:
        raw_name = tc.get("name")
        args = _COMMON.parse_raw_args(tc)
    else:
        raw_name = raw_payload.get("tool_name")
        ti = raw_payload.get("tool_input")
        args = {k: _COMMON.unwrap_arg_value(v) for k, v in ti.items()} if isinstance(ti, dict) else {}

    if not isinstance(raw_name, str) or not raw_name.strip():
        return None, "missing tool name in hook payload"

    raw_name = raw_name.strip()
    low = raw_name.lower()
    cwd = raw_payload.get("cwd") if isinstance(raw_payload.get("cwd"), str) and raw_payload["cwd"].strip() else None
    ws = raw_payload.get("workspacePaths") if isinstance(raw_payload.get("workspacePaths"), list) else []
    if cwd is None and ws and isinstance(ws[0], str) and ws[0].strip():
        cwd = ws[0].strip()
    sess_id = raw_payload.get("conversationId") or raw_payload.get("session_id") or ""

    norm = {"session_id": str(sess_id), "cwd": cwd or os.getcwd(), "raw_name": raw_name, "is_agy": is_agy}

    if low in ("run_command", "bash", "powershell", "monitor"):
        tool_name = "PowerShell" if (low == "powershell" or (low == "run_command" and os.name == "nt")) else ("Monitor" if low == "monitor" else "Bash")
        cmd = args.get("CommandLine") if isinstance(args.get("CommandLine"), str) else args.get("command", "")
        cmd_cwd = args.get("Cwd") if isinstance(args.get("Cwd"), str) and args["Cwd"].strip() else cwd
        norm["cwd"] = cmd_cwd or norm["cwd"]
        norm["tool_name"] = tool_name
        norm["tool_input"] = {"command": cmd if isinstance(cmd, str) else ""}
        if "WaitMsBeforeAsync" in args:
            norm["tool_input"]["WaitMsBeforeAsync"] = args["WaitMsBeforeAsync"]
        return norm, None

    if low in ("read_file", "view_file", "view_file_outline", "view_code_item", "list_dir"):
        p = args.get("Path") or args.get("AbsolutePath") or args.get("DirectoryPath") or args.get("file_path") or args.get("path") or ""
        norm["tool_name"] = "Read"
        norm["tool_input"] = {"file_path": p}
        return norm, None

    if low == "grep_search":
        p = args.get("SearchPath") or args.get("path") or args.get("file_path") or ""
        norm["tool_name"] = "Grep"
        norm["tool_input"] = {"path": p}
        return norm, None

    if low == "find_by_name":
        base = args.get("SearchDirectory") or args.get("path") or ""
        pat = args.get("Pattern") or args.get("pattern") or ""
        norm["tool_name"] = "Glob"
        norm["tool_input"] = {"path": base, "pattern": pat}
        return norm, None

    if low in ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"):
        p = args.get("Path") or args.get("TargetFile") or args.get("file_path") or args.get("path") or ""
        norm["tool_name"] = "Write" if low in ("write_file", "write_to_file") else "Edit"
        norm["tool_input"] = {"file_path": p}
        return norm, None

    if low in ("send_message", "post_message"):
        norm["tool_name"] = "SendMessage"
        norm["tool_input"] = dict(args)
        return norm, None

    if low == "call_mcp_tool" or low.startswith("mcp"):
        vname, act, inner = _extract_mcp_virtual_name_and_args(raw_name, args)
        norm["tool_name"] = vname
        norm["mcp_action_raw"] = act
        norm["tool_input"] = dict(inner)
        if act in ("apply_edits",) or act.endswith("_apply_edits"):
            edit_paths = [e["path"] for e in (inner.get("edits") or []) if isinstance(e, dict) and isinstance(e.get("path"), str)]
            norm["extra_write_paths"] = edit_paths
        elif act in ("run_checks",) or act.endswith("_run_checks"):
            norm["extra_shell_cmds"] = [c["command"] for c in (inner.get("commands") or []) if isinstance(c, dict) and isinstance(c.get("command"), str)]
        return norm, None

    norm["tool_name"] = raw_name
    norm["tool_input"] = dict(args)
    return norm, None


def _ps_colon_path_args(commands, cwd, hg):
    """Extract `(cmd_name, path)` pairs from PowerShell `-Path:val` / `-LiteralPath:val` / `-FilePath:val`."""
    out = []
    for name, rest in commands:
        for tok in rest:
            m = PS_PATH_ARG_RE.match(tok)
            if m:
                out.append((name, hg.arg_path(m.group(1), cwd)))
    return out


def _custom_director_protect(targets, protect_dirs, profile, hg):
    """Enforce `--protect DIRECTOR` over orders, logs, config, tools, adapters, and brief-history for workers."""
    if not protect_dirs or profile == "director":
        return None
    for pd in protect_dirs:
        base_abs = hg.rule_path(str(pd), None).rstrip("/")
        globs = [f"{base_abs}/{rel.lstrip('/')}" for rel in DIRECTOR_PROTECTED_REL]
        for t in targets:
            if t and hg.globs_match(globs, [], t, base_abs):
                return f"DIRECTOR protected path `{t}` cannot be modified by a worker (--protect)"
    return None


def _collect_write_paths(call, hg):
    cwd = call.get("cwd")
    ti = call.get("tool_input") or {}
    paths = []
    if call.get("tool_name") in hg.WRITE_TOOLS:
        fp = next((ti[f] for f in hg.PATH_FIELDS if isinstance(ti.get(f), str) and ti[f]), None)
        if fp:
            paths.append(fp)
    for ep in call.get("extra_write_paths") or []:
        paths.append(ep)
    return [hg.rule_path(p, cwd) for p in paths if p]


def _shell_oauth_targets(commands, cmd_str, cwd, hg):
    """Extract candidate OAuth/Gemini credential paths from shell command arguments & `<` redirections."""
    targets = []
    script_hit = False
    for m in INPUT_REDIR_RE.finditer(cmd_str or ""):
        raw_t = m.group(1) or m.group(2) or m.group(3)
        if raw_t:
            targets.append(hg.arg_path(raw_t, cwd))
    for cmd_nm, rest in commands:
        cname = cmd_nm.lower()
        if cname not in SHELL_OAUTH_READ_CMDS:
            continue
        for tok in rest:
            if cname in SHELL_SCRIPT_INTERPRETERS and OAUTH_TEXT_RE.search(tok):
                script_hit = True
            m_ps = PS_PATH_ARG_RE.match(tok) or PS_INFILE_ARG_RE.match(tok)
            if m_ps:
                targets.append(hg.arg_path(m_ps.group(1).lstrip("@"), cwd))
                continue
            if "@" in tok:
                after_at = tok.split("@", 1)[1].strip("'\"")
                if after_at:
                    targets.append(hg.arg_path(after_at, cwd))
            if tok.startswith("-") and "=" in tok:
                rhs = tok.split("=", 1)[1].lstrip("@").strip("'\"")
                if rhs:
                    targets.append(hg.arg_path(rhs, cwd))
            elif cname == "dd" and tok.lower().startswith("if="):
                rhs = tok[3:].strip("'\"")
                if rhs:
                    targets.append(hg.arg_path(rhs, cwd))
            elif len(tok) > 2 and tok[0] == "-" and not tok.startswith("--") and tok[1] in "dFTfr":
                rhs = tok[2:].lstrip("@").strip("'\"")
                if rhs:
                    targets.append(hg.arg_path(rhs, cwd))
            elif not tok.startswith("-"):
                targets.append(hg.arg_path(tok, cwd))
    return targets, script_hit


def _extra_hits(call, rules_data, rules_path, profile, protect_dirs, hg):
    """Evaluate Antigravity G43, G53, PowerShell colon-flag G50, `--protect`, and MCP batch writes/shells."""
    hits = []
    cwd = call.get("cwd")
    home = hg.rule_path(str(pathlib.Path.home()), None)
    tool = call.get("tool_name") or ""
    ti = call.get("tool_input") or {}
    cmd = ti.get("command") if isinstance(ti.get("command"), str) else ""
    flavor = "powershell" if tool == "PowerShell" else "bash"
    is_shell = tool in hg.SHELL_TOOLS and isinstance(ti.get("command"), str)
    commands = list(hg.simple_commands(hg.FD_DUP.sub(" ", cmd), flavor)) if is_shell else []
    redirects = hg.redirect_targets(cmd, flavor) if is_shell else []

    write_targets = _collect_write_paths(call, hg)
    # 1. Self-protection G00 for agy_guard.py / spec_gate_hook.py / GATE_RULES.json / rules_path
    own_files = {
        hg.rule_path(str(pathlib.Path(__file__)), None),
        hg.rule_path(str(HERE / "spec_gate_hook.py"), None),
        hg.rule_path(str(HERE / "GATE_RULES.json"), None),
        hg.rule_path(str(rules_path), None),
        hg.rule_path(str(pathlib.Path(rules_path).with_name("hook_guard.py")), None),
    }
    own_names = {"agy_guard.py", "spec_gate_hook.py", "gate_rules.json", "hook_guard.py", "guard_rules.json"}
    if any(t in own_files or pathlib.PurePosixPath(t).name.lower() in own_names for t in write_targets) or (is_shell and hg.self_write(commands, own_names, redirects)):
        hits.append(("deny" if hg.base_profile(profile) != "director" else "ask", "G00",
                     "the hook guard scripts and rules files cannot be modified in a session"))

    # 2. G43 Antigravity config/hooks/plugins/rules
    g43_dec = "deny" if hg.base_profile(profile) != "director" else "ask"
    if any(hg.globs_match(AGY_CONFIG_GLOBS, [], t, home) for t in write_targets) or (
        is_shell and hg.shell_write_match({"globs": AGY_CONFIG_GLOBS, "except": []}, commands, redirects, cwd, home, cmd)
    ):
        hits.append((g43_dec, "G43", "Antigravity hooks, plugins and .agent/rules/ are managed by agy_setup.py or the PO/Director"))

    # 3. G53 OAuth credentials (file tools + shell readers) + G50 PowerShell -Path:.env colon-flag gap
    read_targets = []
    oauth_script_hit = False
    if tool in ("Read", "Grep", "Glob", "NotebookRead"):
        for f in ("file_path", "path", "notebook_path"):
            if isinstance(ti.get(f), str) and ti[f]:
                read_targets.append(hg.rule_path(ti[f], cwd))
        if isinstance(ti.get("pattern"), str) and ti["pattern"]:
            base = ti.get("path") or cwd or os.getcwd()
            read_targets.append(hg.rule_path(os.path.join(base, ti["pattern"]), cwd))
    if is_shell:
        ps_colon_args = _ps_colon_path_args(commands, cwd, hg)
        read_targets.extend(pt for _, pt in ps_colon_args)
        sh_oauth_targets, oauth_script_hit = _shell_oauth_targets(commands, cmd, cwd, hg)
        read_targets.extend(sh_oauth_targets)
        # Match PowerShell colon-flag read targets ONLY when command verb matches a G50 shell reader
        g50 = next((r for r in rules_data.get("rules", []) if r.get("id") == "G50"), None)
        if g50 and ps_colon_args:
            for m in hg.as_list(g50["match"]):
                if m.get("kind") == "shell" and any(
                    any(re.fullmatch(c, cmd_nm, re.I) for c in m.get("cmd", ()))
                    and hg.globs_match(m.get("path_args", []), m.get("path_except", []), pt, home)
                    for cmd_nm, pt in ps_colon_args
                ):
                    hits.append(("deny", "G50", g50["reason"]))
                    break
    if oauth_script_hit or any(hg.globs_match(AGY_OAUTH_GLOBS, [], t, home) for t in read_targets):
        hits.append(("deny", "G53", "OAuth and Gemini credentials are not read in a session"))

    # 4. Protected DIRECTOR paths for worker (covers Write/Edit AND mcp apply_edits / shell writes)
    prot_check_targets = list(write_targets)
    if prot_check_targets:
        why_prot = _custom_director_protect(prot_check_targets, protect_dirs, hg.base_profile(profile), hg)
        if why_prot:
            hits.append(("deny", "PROTECT", why_prot))
    if is_shell and protect_dirs and hg.base_profile(profile) != "director":
        for pd in protect_dirs:
            base_abs = hg.rule_path(str(pd), None).rstrip("/")
            globs = [f"{base_abs}/{rel.lstrip('/')}" for rel in DIRECTOR_PROTECTED_REL]
            if hg.shell_write_match({"globs": globs, "except": []}, commands, redirects, cwd, home, cmd):
                hits.append(("deny", "PROTECT", f"shell write to protected `{base_abs}` is blocked for workers"))
                break

    # 5. run_checks MCP multiple shell commands
    for extra_cmd in call.get("extra_shell_cmds") or []:
        sub_call = {**call, "tool_name": "PowerShell" if os.name == "nt" else "Bash", "tool_input": {"command": extra_cmd}}
        sub_call.pop("extra_shell_cmds", None)
        sub_call.pop("extra_write_paths", None)
        hits.extend(hg.decide(rules_data, profile, sub_call, rules_path))
        hits.extend(_extra_hits(sub_call, rules_data, rules_path, profile, protect_dirs, hg))

    return hits


def _queue_bridge_alert(bridge_state, conv_id, msg):
    if not bridge_state or not msg:
        return
    try:
        hb = _COMMON.load_sibling(HERE, "agy_hook_bridge", prefix="_agy_gd_hb_")
        hb.queue_alert(bridge_state, conv_id, msg)
    except Exception:
        pass


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", default=str(_COMMON.find_base_tools(HERE) / "guard_rules.json"), help="rules JSON (default: tools/guard_rules.json)")
    ap.add_argument("--profile", choices=("worker", "director", "cleaner"), default="worker")
    ap.add_argument("--mode", choices=("remind", "ask", "block"), default="remind")
    ap.add_argument("--protect", action="append", default=[], help="DIRECTOR folder protected from worker edits")
    ap.add_argument("--pause-flag", help="PAUSE.flag file (pause_ctl.py on)")
    ap.add_argument("--sessions", help=".sessions.json per-conversation role map")
    ap.add_argument("--audit", help="hash-chained action audit log template")
    ap.add_argument("--bridge-state", help="state dir to queue remind/loop warnings for PreInvocation")
    ap.add_argument("--loop-guard", action="store_true")
    ap.add_argument("--loop-config", help="loop_guard JSON config")
    ap.add_argument("--loop-mode", choices=("remind", "ask"), default="remind")
    ap.add_argument("--overwrite-utf8", action="store_true", help="inject -X utf8 on bare python run_command calls")
    ap.add_argument("--hard-only", action="store_true", help="enforce hard rules only (G00/G50-G53/PROTECT, G43 for workers); relax soft remind/ask")
    ap.add_argument("--format", choices=("antigravity", "claude"), default="antigravity")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    try:
        hg = _COMMON.load_base_tool("hook_guard", here=HERE, prefix="_agy_gd_")
        rules_data = hg.load_rules(a.rules)
        sessions = hg.load_sessions(a.sessions) if a.sessions else None
        loop = None
        loop_conf = None
        if a.loop_guard:
            if not a.audit:
                raise ValueError("--loop-guard requires --audit")
            loop = _COMMON.load_base_tool("loop_guard", here=HERE, prefix="_agy_gd_")
            loop_conf = loop.load_config(a.loop_config)
    except Exception as exc:
        print(f"agy_guard configuration error: {exc}", file=sys.stderr)
        return _emit(a.format, "deny", f"agy_guard config error ({exc}); fail-closed") if a.format == "antigravity" else 1

    raw_text = sys.stdin.read() if sys.stdin is not None else ""
    if not raw_text.strip():
        return _emit(a.format, "deny", "agy_guard: empty hook stdin (fail-closed)")
    try:
        raw_payload = json.loads(raw_text)
    except ValueError as exc:
        return _emit(a.format, "deny", f"agy_guard: malformed JSON stdin ({exc}); fail-closed")
    if not isinstance(raw_payload, dict):
        return _emit(a.format, "deny", "agy_guard: hook stdin must be a JSON object (fail-closed)")

    call, norm_err = _normalize_call(raw_payload)
    if norm_err:
        return _emit(a.format, "deny", f"agy_guard: {norm_err} (fail-closed)")

    profile = a.profile
    if sessions is not None:
        profile = hg.session_profile(sessions, call, profile)

    # Strip heredocs before inspection cleanly
    if call.get("tool_name") in ("Bash", "Monitor") and isinstance((call.get("tool_input") or {}).get("command"), str):
        call = dict(call, tool_input=dict(call["tool_input"], command=hg.strip_heredocs(call["tool_input"]["command"])))

    def log_decision(dec, rule_id):
        if not a.audit:
            return
        try:
            audit_mod = _COMMON.load_base_tool("audit_log", here=HERE, prefix="_agy_gd_")
            try:
                agy_aud = _COMMON.load_sibling(HERE, "agy_audit", prefix="_agy_gd_")
                target = agy_aud.resolve_worktree_audit_path(a.audit, raw_payload, audit_mod, tool_cwd=call.get("cwd"))
            except Exception:
                target = audit_mod.expand(a.audit, folder=call.get("cwd"))
            audit_mod.append_entry(target, audit_mod.decision_entry(call, dec, rule_id))
        except Exception:
            pass

    # 1. Pause flag (bypassed only for Director role when .sessions.json distinguishes Director)
    paused = hg.pause_state(a.pause_flag) if (a.pause_flag and not (sessions is not None and profile == "director")) else None
    if paused:
        allow_pause = hg.pause_allows(rules_data, profile, call)
        if not allow_pause and (call.get("mcp_action_raw") in SAFE_AGY_FAST_READS):
            allow_pause = True
        if not allow_pause:
            msg = f"PAUSED by {paused[0]} at {paused[1]}: {paused[2]}; {rules_data['pause']['reason']}"
            log_decision("pause", "PAUSE")
            return _emit(a.format, "deny", msg)

    # 2. Builtin checks + rules + Antigravity extras
    protect_dirs = list(a.protect or [])
    if sessions and isinstance(sessions.get("protected"), list) and not protect_dirs:
        pass  # sessions['protected'] is evaluated below
    why_builtin = None
    why_prot = None
    label = "builtin"
    ti = call.get("tool_input") or {}
    cmd_str = ti.get("command") if isinstance(ti.get("command"), str) else None
    if protect_dirs and hg.base_profile(profile) != "director" and call.get("tool_name") in ("Write", "Edit") and isinstance(ti.get("file_path"), str) and ti["file_path"]:
        why_prot = hg.protected_reason(ti["file_path"], call.get("cwd"), protect_dirs)
    elif isinstance(cmd_str, str):
        why_builtin = hg.reason(cmd_str, "powershell" if call.get("tool_name") == "PowerShell" else "bash")
        if sessions is not None and call.get("tool_name") in hg.SHELL_TOOLS:
            why_prot = hg.protected_delete(
                rules_data,
                list(hg.simple_commands(hg.FD_DUP.sub(" ", cmd_str), "powershell" if call.get("tool_name") == "PowerShell" else "bash")),
                call.get("cwd"),
                sessions.get("protected", []),
            )

    # Skip base mcp_match on safe Antigravity quick/read tools
    if call.get("mcp_action_raw") in SAFE_AGY_FAST_READS:
        hits = []
    else:
        hits = hg.decide(rules_data, profile, call, a.rules)
    if why_prot:
        hits.append(("deny", "PROTECT", why_prot))
    hits.extend(_extra_hits(call, rules_data, a.rules, profile, protect_dirs, hg))

    # G43 and G00 and secret/PROTECT are hard-denied for workers across ALL modes (incl. --hard-only / --beginner)
    hard_ids = {"G00", "G50", "G51", "G52", "G53", "PROTECT"}
    if hg.base_profile(profile) != "director":
        hard_ids.add("G43")
    hard_hits = [h for h in hits if h[0] == "deny" and h[1] in hard_ids]
    if hard_hits:
        hard_hits.sort(key=lambda h: (0 if h[1].startswith("G") else 1, h[1]))
        rid, rmsg = hard_hits[0][1], hard_hits[0][2]
        log_decision("deny", rid)
        return _emit(a.format, "deny", f"BLOCKED by agy_guard [{rid}]: {rmsg}")

    if a.hard_only:
        overwrite = None
        if a.overwrite_utf8 and call.get("raw_name", "").lower() == "run_command" and isinstance(cmd_str, str):
            patched = _inject_utf8_flag(cmd_str)
            if patched:
                overwrite = {"CommandLine": patched}
        return _emit(a.format, "allow", overwrite=overwrite)

    deny = next((h for h in sorted(hits, key=lambda x: (0 if x[1].startswith("G") else 1, x[1])) if h[0] == "deny"), None)
    ask = next((h for h in sorted(hits, key=lambda x: (0 if x[1].startswith("G") else 1, x[1])) if h[0] == "ask"), None)

    if a.mode != "block" and (deny or why_builtin or ask):
        rid, rmsg = (deny[1], deny[2]) if deny else (label, why_builtin) if why_builtin else (ask[1], ask[2])
        line = f"agy_guard [{rid}]: {rmsg}"
        if a.mode == "ask":
            log_decision("ask", rid)
            return _emit(a.format, "ask", f"[{rid}] {rmsg}")
        log_decision("remind", rid)
        _queue_bridge_alert(a.bridge_state, call.get("session_id"), line)
        return _emit(a.format, "allow", reason=line)

    if deny or why_builtin:
        rid = deny[1] if deny else label
        rmsg = deny[2] if deny else why_builtin
        log_decision("deny", rid)
        if a.pause_flag and not paused and not (sessions is not None and profile == "director"):
            conf = (rules_data.get("auto_pause") or {}).get(hg.base_profile(profile))
            paused_now = hg.auto_pause(a.pause_flag, conf, rid)
            if paused_now:
                log_decision("pause", "AUTO")
        return _emit(a.format, "deny", f"BLOCKED by agy_guard [{rid}]: {rmsg}")

    if ask:
        log_decision("ask", ask[1])
        return _emit(a.format, "ask", f"[{ask[1]}] {ask[2]}")

    # Optional --loop-guard
    if loop is not None:
        found = loop.hook_reminder(call, a.audit, loop_conf)
        if found:
            rid, rmsg = f"LOOP:{found[0]}", found[1]
            log_decision(a.loop_mode, rid)
            if a.loop_mode == "ask":
                return _emit(a.format, "ask", rmsg)
            _queue_bridge_alert(a.bridge_state, call.get("session_id"), rmsg)
            return _emit(a.format, "allow", reason=rmsg)

    # Optional --overwrite-utf8 for run_command
    overwrite = None
    if a.overwrite_utf8 and call.get("raw_name", "").lower() == "run_command" and isinstance(cmd_str, str):
        patched = _inject_utf8_flag(cmd_str)
        if patched:
            overwrite = {"CommandLine": patched}

    return _emit(a.format, "allow", overwrite=overwrite)


if __name__ == "__main__":
    sys.exit(main())
