#!/usr/bin/env python3
"""Antigravity PreToolUse guard (dual-protocol: Antigravity camelCase + Claude snake_case).

Wraps upstream tools/hook_guard.py and tools/guard_rules.json without modifying them, translating
Antigravity tool names and arguments (App 2.0, IDE/CLI, and native MCP calls) into hook_guard's
canonical rules engine and emitting Antigravity PreToolUse JSON decisions:
  allow       clean call, or remind-mode warning (with reason + optional bridge-state reminder)
  force_ask   flagged in --mode ask (prompts user, ignoring cached Always-Allow grants)
  deny        hard blocks (G00 self-edit, G50-G53 secrets/credentials, worker G43 config/hooks
              writes or moves, --protect DIRECTOR governance/ledger/state/tools, --pause-flag,
              empty/malformed payload, or --mode block)

Supports in-process calls via `run_from_raw(raw_stdin, argv, emit=False)` for single-process
plugin seatbelt delegation.
"""
import argparse
import datetime
import functools
import importlib.util
import io
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    p = HERE / "agy_common.py"
    spec = importlib.util.spec_from_file_location("_agy_guard_common", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()

AGY_WRITE_TOOLS = ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content")
AGY_READ_TOOLS = ("read_file", "view_file", "list_dir")
AGY_SEARCH_TOOLS = ("grep_search", "find_by_name")
AGY_INTERNAL_REPORT_TOOLS = ("send_message", "post_message", "SendMessage", "mcp__ccd_session_mgmt__send_message")

# Antigravity-specific config/rule files (same protection policy as G40; hard-denied for non-directors)
AGY_CONFIG_GLOBS = [
    "**/.agents",
    "**/.agents/**",
    "**/.agents/hooks*.json",
    "**/director-work/hooks.json",
    "**/.agents/plugins.json",
    "**/.agents/skills.json",
    "**/.agents/rules/**",
    "**/AGENTS.md",
    "**/AGENTS.local.md",
    "**/GEMINI.md",
    "~/.gemini/config/**",
    "~/.gemini/antigravity-cli/settings.json",
    "~/.gemini/settings.json",
]

# Extra Antigravity credential locations on top of G50
AGY_EXTRA_SECRET_GLOBS = [
    "**/.gemini/**/oauth_creds.json",
    "**/.gemini/**/*.credentials.json",
    "**/.gemini/**/accounts.json",
]

AGY_PROTECTED_FILES = ("agy_guard.py", "agy_common.py", "agy_hook_bridge.py", "agy_audit.py", "seatbelt_hook.py", "hook_guard.py", "guard_rules.json")

_PS_FLAGS_WITH_VALUE = {
    "-executionpolicy", "-ep", "-windowstyle", "-w", "-inputformat", "-if",
    "-outputformat", "-of", "-version", "-v", "-workingdirectory", "-wd",
    "-configurationname", "-settingsfile", "-file", "-f",
}
_COLON_READ_OPT_RE = re.compile(r"^-(?:literal)?path:(.+)$", re.I)
_COLON_WRITE_OPT_RE = re.compile(r"^-(?:destination|filepath|outfile):(.+)$|^--(?:file|output)=(.+)$", re.I)
_PS_WRITE_VERBS = {
    "set-content", "sc", "add-content", "ac", "out-file", "clear-content", "clc",
    "new-item", "ni", "set-item", "si", "remove-item", "ri", "del", "erase", "rm",
    "move-item", "mi", "mv", "move", "rename-item", "rni", "ren", "rename",
    "copy-item", "cpi", "cp", "copy", "tee", "tee-object",
}


def load_sibling(name):
    """Load and cache a sibling module by file name without polluting sys.path."""
    return _COMMON.load_sibling(HERE, name, prefix="_agy_sib_")


def detect_format(payload, requested):
    """Return 'antigravity' or 'claude' from explicit --format or payload keys."""
    if requested != "auto":
        return requested
    if "toolCall" in payload or "hookEventName" in payload or "workspacePaths" in payload:
        return "antigravity"
    return "claude"


def first_str(args, *keys):
    """Return first non-empty string value among keys in args dict."""
    for key in keys:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def resolve_shell_name(shell_opt):
    """Choose 'PowerShell' or 'Bash' for hook_guard canonical shell parsing."""
    if shell_opt == "powershell":
        return "PowerShell"
    if shell_opt == "bash":
        return "Bash"
    return "PowerShell" if os.name == "nt" else "Bash"


def _unwrap_arg(val):
    return _COMMON.unwrap_arg_value(val)


def _win_clean_path_str(val):
    """Normalize Windows NTFS :stream names and trailing spaces/dots on path segments."""
    if not val or not isinstance(val, str):
        return val
    s = val.replace("\\", "/")
    drive = ""
    rest = s
    if len(s) >= 2 and s[0].isalpha() and s[1] == ":":
        drive = s[:2]
        rest = s[2:]
    cleaned = []
    for seg in rest.split("/"):
        if ":" in seg and not (len(seg) == 2 and seg[0].isalpha() and seg[1] == ":"):
            seg = seg.split(":", 1)[0]
        if seg not in ("", ".", ".."):
            seg = seg.rstrip(" .")
        cleaned.append(seg)
    return drive + "/".join(cleaned)


def _rule_path_variants(hg, val, cwd):
    """Return rule_path(val) plus NTFS-stream / trailing-space-trimmed variant when distinct."""
    if not isinstance(val, str) or not val.strip():
        return []
    out = [hg.rule_path(val, cwd)]
    base = hg.plain(val) or val
    cleaned = _win_clean_path_str(base)
    if cleaned and cleaned != val:
        rp2 = hg.rule_path(cleaned, cwd)
        if rp2 not in out:
            out.append(rp2)
    return out


def _arg_path_variants(hg, word, cwd):
    """Return arg_path(word) plus trailing-space / :stream cleaned variant."""
    if not isinstance(word, str) or not word.strip():
        return []
    out = [hg.arg_path(word, cwd)]
    cleaned = _win_clean_path_str(hg.plain(word) or word)
    if cleaned and cleaned != word:
        ap2 = hg.arg_path(cleaned, cwd)
        if ap2 not in out:
            out.append(ap2)
    return out


def translate_antigravity_call(payload, shell_opt="auto"):
    """Translate an Antigravity PreToolUse stdin payload into canonical hook_guard dict + metadata."""
    tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    raw_name = tc.get("name") if isinstance(tc.get("name"), str) else ""
    raw_args = _COMMON.parse_raw_args(tc)
    args = {k: _unwrap_arg(v) for k, v in raw_args.items()} if isinstance(raw_args, dict) else {}
    ws = payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
    tool_cwd = first_str(args, "Cwd", "cwd", "workspace")
    cwd = tool_cwd or (payload.get("cwd") if isinstance(payload.get("cwd"), str) else None) or (ws[0] if ws and isinstance(ws[0], str) else None)
    conv_id = payload.get("conversationId") if isinstance(payload.get("conversationId"), str) else None
    step_idx = payload.get("stepIdx")

    canonical = {
        "session_id": conv_id,
        "agent_id": None,
        "tool_use_id": tc.get("id") if isinstance(tc.get("id"), str) else (f"step-{step_idx}" if step_idx is not None else None),
        "cwd": cwd,
        "tool_name": raw_name,
        "tool_input": dict(args),
    }

    low = raw_name.lower()
    if low == "run_command":
        canonical["tool_name"] = resolve_shell_name(shell_opt)
        cmd = first_str(args, "CommandLine", "command") or ""
        canonical["tool_input"] = {"command": cmd}
    elif low in AGY_READ_TOOLS:
        canonical["tool_name"] = "Read"
        path = first_str(args, "Path", "AbsolutePath", "DirectoryPath", "file_path", "path")
        canonical["tool_input"] = {"file_path": path} if path else {}
    elif low in ("write_file", "write_to_file"):
        canonical["tool_name"] = "Write"
        path = first_str(args, "Path", "TargetFile", "file_path", "path")
        canonical["tool_input"] = {"file_path": path} if path else {}
    elif low in ("edit_file", "replace_file_content", "multi_replace_file_content"):
        canonical["tool_name"] = "Edit"
        path = first_str(args, "Path", "TargetFile", "file_path", "path")
        canonical["tool_input"] = {"file_path": path} if path else {}
    elif low == "grep_search":
        canonical["tool_name"] = "Grep"
        path = first_str(args, "SearchPath", "path")
        inc = args.get("Includes")
        inc_first = inc if isinstance(inc, str) else (inc[0] if isinstance(inc, list) and inc and isinstance(inc[0], str) else None)
        ti = {}
        if path:
            ti["path"] = path
        if isinstance(args.get("Query"), str) and args["Query"]:
            ti["pattern"] = args["Query"]
        if inc_first:
            ti["glob"] = inc_first
        canonical["tool_input"] = ti
    elif low == "find_by_name":
        canonical["tool_name"] = "Glob"
        path = first_str(args, "SearchDirectory", "path")
        ti = {}
        if path:
            ti["path"] = path
        if isinstance(args.get("Pattern"), str) and args["Pattern"]:
            ti["pattern"] = args["Pattern"]
        canonical["tool_input"] = ti
    elif low == "call_mcp_tool":
        srv = first_str(args, "ServerName", "serverName", "server_name") or "unknown"
        tname = first_str(args, "ToolName", "toolName", "tool_name") or "unknown"
        mcp_args = args.get("Arguments") if args.get("Arguments") is not None else args.get("arguments")
        if isinstance(mcp_args, str):
            try:
                mcp_args = json.loads(mcp_args)
            except ValueError:
                mcp_args = {}
        canonical["tool_name"] = f"mcp__{srv}__{tname}"
        canonical["tool_input"] = mcp_args if isinstance(mcp_args, dict) else {}
    elif raw_name.startswith("mcp_") and not raw_name.startswith("mcp__"):
        rest = raw_name[4:]
        if "_" in rest:
            srv, act = rest.split("_", 1)
        else:
            srv, act = "unknown", rest
        canonical["tool_name"] = f"mcp__{srv}__{act}"
    elif raw_name in AGY_INTERNAL_REPORT_TOOLS:
        canonical["tool_name"] = "SendMessage"

    return canonical, raw_name, args


def pause_allows_agy(hg, data, profile, canonical, raw_name, raw_args):
    """Extend hook_guard.pause_allows for Antigravity read/search/internal tools and paths."""
    if raw_name in AGY_INTERNAL_REPORT_TOOLS:
        return True
    if raw_name.lower() in AGY_READ_TOOLS + AGY_SEARCH_TOOLS or raw_name in (
        "update_focus", "update_steps", "read_board", "swarm_wait", "list_resources", "read_resource"
    ):
        return True
    return hg.pause_allows(data, profile, canonical)


def _secret_rule_globs(data):
    """Collect G50 secret path globs from loaded rules data."""
    if not isinstance(data, dict):
        return [], []
    globs, exc = [], []
    for r in data.get("rules") or []:
        if r.get("id") != "G50":
            continue
        for m in r.get("match") if isinstance(r.get("match"), list) else ([r.get("match")] if isinstance(r.get("match"), dict) else []):
            if isinstance(m, dict) and m.get("kind") == "path":
                globs.extend(m.get("globs") or [])
                exc.extend(m.get("except") or [])
    return globs, exc


def _extract_extra_shell_info(hg, commands):
    """Extract colon-bound PS opts, dd if=/of=, git show rev:path, and positional powershell subcommands."""
    extra_reads = []
    extra_writes = []
    extra_ps_strings = []
    for name, rest in commands:
        is_ps_write = name in _PS_WRITE_VERBS
        for tok in rest:
            mr = _COLON_READ_OPT_RE.match(tok)
            if mr:
                val = mr.group(1).strip().strip("\"'")
                if val:
                    extra_reads.append(val)
                    if is_ps_write:
                        extra_writes.append(val)
                continue
            mw = _COLON_WRITE_OPT_RE.match(tok)
            if mw:
                val = next((g for g in mw.groups() if g), "").strip().strip("\"'")
                if val:
                    extra_writes.append(val)
                    extra_reads.append(val)
                continue
            if name == "dd":
                low_t = tok.lower()
                if low_t.startswith("of=") and len(tok) > 3:
                    extra_writes.append(tok[3:].strip("\"'"))
                elif low_t.startswith("if=") and len(tok) > 3:
                    extra_reads.append(tok[3:].strip("\"'"))
        if name == "git":
            pos = hg.positional("git", rest)
            if pos and pos[0] == "show":
                for tok in rest:
                    if not tok.startswith("-") and ":" in tok:
                        _, rhs = tok.split(":", 1)
                        if rhs:
                            extra_reads.append(rhs)
        if name in ("powershell", "pwsh") and hg.string_run(name, rest) is None:
            i = 0
            while i < len(rest):
                t = rest[i]
                tl = t.lower()
                if tl in _PS_FLAGS_WITH_VALUE:
                    i += 2
                    continue
                if t.startswith(("-", "/")):
                    i += 1
                    continue
                extra_ps_strings.append(t)
                break
    return extra_reads, extra_writes, extra_ps_strings


def agy_extra_hits(hg, data, profile, canonical, raw_name, rules_path, raw_args=None, pre_cmds=None, pre_targets=None):
    """Check Antigravity-specific guard rules without altering upstream guard_rules.json."""
    hits = []
    tool = canonical.get("tool_name") or ""
    tool_input = canonical.get("tool_input") or {}
    cwd = canonical.get("cwd")
    home = hg.rule_path(str(pathlib.Path.home()), None)
    base_prof = hg.base_profile(profile)
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    shell_call = tool in hg.SHELL_TOOLS and isinstance(command, str)
    shell = "powershell" if tool == "PowerShell" else "bash"
    commands = pre_cmds if pre_cmds is not None else (list(hg.simple_commands(hg.FD_DUP.sub(" ", command), shell)) if shell_call else [])
    targets = pre_targets if pre_targets is not None else (hg.redirect_targets(command, shell) if shell_call else [])

    extra_reads, extra_writes, extra_ps = _extract_extra_shell_info(hg, commands) if shell_call else ([], [], [])
    for ps_cmd in extra_ps:
        commands.extend(hg.simple_commands(hg.FD_DUP.sub(" ", ps_cmd), "powershell"))
        targets.extend(hg.redirect_targets(ps_cmd, "powershell"))

    raw_path = next((tool_input[f] for f in hg.PATH_FIELDS if isinstance(tool_input, dict) and isinstance(tool_input.get(f), str) and tool_input[f]), None)
    path_variants = _rule_path_variants(hg, raw_path, cwd) if raw_path else []
    target = path_variants[0] if path_variants else None

    # Collect all read/search target candidates (covers path + cleaned NTFS/trailing space + pattern + Includes[0:])
    search_targets = list(path_variants)
    extra_inc_targets = []
    if tool in ("Read", "Grep", "Glob"):
        pat_items = []
        if tool == "Glob" and isinstance(tool_input.get("pattern"), str) and tool_input["pattern"]:
            pat_items.append(tool_input["pattern"])
        if isinstance(tool_input.get("glob"), str) and tool_input["glob"]:
            pat_items.append(tool_input["glob"])
        if isinstance(raw_args, dict):
            inc = raw_args.get("Includes")
            if isinstance(inc, str) and inc.strip():
                pat_items.append(inc.strip())
            elif isinstance(inc, list):
                pat_items.extend(x.strip() for x in inc if isinstance(x, str) and x.strip())
        base_dir = raw_path or cwd or os.getcwd()
        for idx_pat, pat in enumerate(pat_items):
            for cand in (os.path.join(base_dir, pat), os.path.join(base_dir, pat.lstrip("*")), pat):
                for rv in _rule_path_variants(hg, cand, cwd):
                    if rv not in search_targets:
                        search_targets.append(rv)
                    if idx_pat > 0 and rv not in extra_inc_targets:
                        extra_inc_targets.append(rv)

    # Protect agy_*.py / seatbelt_hook.py alongside G00
    protected_scripts = {hg.rule_path(str(HERE / name), None) for name in AGY_PROTECTED_FILES}
    sb_repo = HERE.parent / "plugin" / "director-work" / "scripts" / "seatbelt_hook.py"
    protected_scripts.add(hg.rule_path(str(sb_repo), None))
    if rules_path:
        protected_scripts.add(hg.rule_path(str(rules_path), None))
    names = {n.lower() for n in AGY_PROTECTED_FILES}
    if rules_path:
        names.add(pathlib.Path(str(rules_path)).name.lower())
    is_sb_write = tool in hg.WRITE_TOOLS and any(pv.endswith("/scripts/seatbelt_hook.py") for pv in path_variants)
    extra_self_write = any(os.path.basename(w.rstrip("/\\")).lower() in names for w in extra_writes)
    if (tool in hg.WRITE_TOOLS and (any(pv in protected_scripts for pv in path_variants) or is_sb_write)) or (
        shell_call and (hg.self_write(commands, names, targets) or extra_self_write)
    ):
        hits.append((hg.SELF_RULE["decision"][base_prof], "G00", hg.SELF_RULE["reason"]))

    # G43 (Antigravity config/rule files protection, hard-denied for non-directors)
    cfg_decision = "deny" if base_prof != "director" else "ask"
    cfg_reason = "Antigravity hooks, plugins and AGENTS.md/GEMINI.md rules are managed by the PO or Director (docs/08); report the change you need"
    g43_match = {"globs": AGY_CONFIG_GLOBS, "except": []}
    if tool in hg.WRITE_TOOLS and any(hg.globs_match(AGY_CONFIG_GLOBS, [], pv, home) for pv in path_variants):
        hits.append((cfg_decision, "G43", cfg_reason))
    elif shell_call and (
        hg.shell_write_match(g43_match, commands, targets, cwd, home, command)
        or (data is not None and hg.protected_delete(data, commands, cwd, AGY_CONFIG_GLOBS))
        or any(hg.globs_match(AGY_CONFIG_GLOBS, [], ap, home) for w in extra_writes for ap in _arg_path_variants(hg, w, cwd))
    ):
        hits.append((cfg_decision, "G43", cfg_reason))

    # G53 + G50 completeness across Read/Grep/Glob, NTFS/trailing-space bypasses, PS -Path:, and git show
    sec_reason = "credential files are not read in a session (docs/08); report what you need to the Director"
    g50_globs, g50_exc = _secret_rule_globs(data)
    if tool in ("Read", "Grep", "Glob") and any(t and hg.globs_match(AGY_EXTRA_SECRET_GLOBS, [], t, home) for t in search_targets):
        hits.append(("deny", "G53", sec_reason))
    elif tool in ("Read", "Grep", "Glob") and g50_globs and any(t and hg.globs_match(g50_globs, g50_exc, t, home) for t in search_targets):
        hits.append(("deny", "G50", sec_reason))
    elif shell_call:
        shell_word_hit = False
        for _, rest in commands:
            for word in rest:
                if word.startswith("-"):
                    continue
                for ap in _arg_path_variants(hg, word, cwd):
                    if hg.globs_match(AGY_EXTRA_SECRET_GLOBS, [], ap, home):
                        hits.append(("deny", "G53", sec_reason))
                        shell_word_hit = True
                        break
                    if g50_globs and ap != _arg_path_variants(hg, word, cwd)[0] and hg.globs_match(g50_globs, g50_exc, ap, home):
                        hits.append(("deny", "G50", sec_reason))
                        shell_word_hit = True
                        break
                if shell_word_hit:
                    break
            if shell_word_hit:
                break
        if not shell_word_hit and (extra_reads or extra_writes):
            for word in extra_reads + extra_writes:
                for ap in _arg_path_variants(hg, word, cwd):
                    if hg.globs_match(AGY_EXTRA_SECRET_GLOBS, [], ap, home):
                        hits.append(("deny", "G53", sec_reason))
                        shell_word_hit = True
                        break
                    if g50_globs and hg.globs_match(g50_globs, g50_exc, ap, home):
                        hits.append(("deny", "G50", sec_reason))
                        shell_word_hit = True
                        break
                if shell_word_hit:
                    break

    # Apply non-G50 path rules against Includes[1:] as well as cleaned Write/Edit paths
    if data is not None and (extra_inc_targets or (tool in hg.WRITE_TOOLS and len(path_variants) > 1)):
        chk_targets = extra_inc_targets if tool in ("Read", "Grep", "Glob") else path_variants[1:]
        for r in data.get("rules") or []:
            dec = hg.rule_decision(r, profile)
            if dec == "none" or r.get("id") == "G50":
                continue
            for m in hg.as_list(r["match"]):
                if m.get("kind") == "path" and hg.path_match(m, tool, chk_targets, home):
                    hits.append((dec, r["id"], r["reason"]))
                    break

    # Check unpacked positional powershell commands through upstream decide + reason
    if data is not None and extra_ps:
        for ps_cmd in extra_ps:
            ps_call = dict(canonical, tool_name="PowerShell", tool_input={"command": ps_cmd})
            hits.extend(hg.decide(data, profile, ps_call, rules_path))

    return hits


def queue_bridge_alert(state_dir, conv_id, message):
    """Persist a short reminder for PreInvocation injection when --bridge-state is configured."""
    if not state_dir:
        return
    try:
        folder = pathlib.Path(state_dir)
        folder.mkdir(parents=True, exist_ok=True)
        cid = re.sub(r"[^A-Za-z0-9._-]", "_", conv_id or "default") or "default"
        path = folder / f"pending_{cid}.jsonl"
        entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "message": message}
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def maybe_utf8_overwrite(raw_name, raw_args):
    """If opt-in --overwrite-utf8 is enabled, inject '-X utf8' on bare/quoted python script invocations."""
    if raw_name.lower() != "run_command":
        return None
    cmd = raw_args.get("CommandLine")
    if not isinstance(cmd, str):
        return None
    m = re.match(
        r'^(\s*)(python(?:3(?:\.\d+)?)?|py)(\s+)(?!.*(?:-X\s*utf8\b|--utf8\b))((?:-[Bu]\s+)*(?:"[^"]+\.py"|\'[^\']+\.py\'|[^\s;|&-][^\s;|&]*\.py\b)(?:\s.*|$))$',
        cmd,
        re.I,
    )
    if not m:
        return None
    new_cmd = f"{m.group(1)}{m.group(2)}{m.group(3)}-X utf8 {m.group(4)}"
    return {"CommandLine": new_cmd}


def emit_antigravity(decision, reason=None, overwrite=None):
    """Print Antigravity PreToolUse JSON response to stdout and return 0."""
    out = {"decision": decision}
    if reason:
        out["reason"] = reason
    if overwrite:
        out["overwrite"] = overwrite
    print(json.dumps(out, ensure_ascii=False))
    return 0


def resolve_audit_path(audit_arg, raw_payload, tool_cwd=None):
    """Expand {worktree} uniformly with agy_audit.py so PreToolUse and PostToolUse share one chain."""
    if not audit_arg:
        return None
    if "{worktree}" not in str(audit_arg):
        return audit_arg
    try:
        aud = load_sibling("audit_log")
        agy_aud = load_sibling("agy_audit")
        return agy_aud.resolve_worktree_audit_path(audit_arg, raw_payload or {}, aud, tool_cwd=tool_cwd)
    except Exception:
        return audit_arg


def _protect_target_globs(hg, protect_dirs):
    """Build guarded directory globs across DIRECTOR governance, ledgers, reviews, audits, and tools."""
    globs = []
    protected_subs = (
        "DIRECTOR_LOG.md", "orders/**", "director_work.json", ".agydw_setup_state.json",
        ".sessions.json", "tools/**", "tools_v*/**", "PO_PENDING.md", "reviews/**",
        "BRIEF.md", "DECISION_REGISTER.md", "DIRECTOR_STATE.md", "evidence/**",
        "audit/**", ".bridge_state/**", "ACTIVE_WORKSTREAMS.md", "GUARDS.md",
        "BASELINE_METRICS.md", "PAUSE.flag",
    )
    for pdir in protect_dirs or []:
        base_rp = hg.rule_path(str(pdir), None).rstrip("/")
        for sub in protected_subs:
            globs.append(f"{base_rp}/{sub}")
    return globs


def evaluate_call(hg, data, sessions, a, canonical, raw_name, raw_args, fmt, conv_id, agent_hint=None, raw_payload=None, tool_cwd=None):
    """Evaluate one translated call using cached hook_guard rules + Antigravity extras."""
    # Install pure glob regex cache once and per-call path resolution cache to prevent O(N) slowdowns
    if not getattr(hg, "_agydw_glob_cached", False):
        hg.glob_regex = functools.lru_cache(maxsize=512)(hg.glob_regex)
        hg._agydw_glob_cached = True

    orig_long_path = hg.long_path
    orig_rule_path = hg.rule_path
    orig_arg_path = hg.arg_path
    lp_cache = {}
    rp_cache = {}
    ap_cache = {}

    def fast_long_path(p):
        key = str(p)
        cached = lp_cache.get(key)
        if cached is not None:
            return cached
        if os.name == "nt" and "~" not in key and p.parent != p and not p.exists():
            res = fast_long_path(p.parent) / p.name
        else:
            res = orig_long_path(p)
        lp_cache[key] = res
        return res

    def fast_rule_path(val, cwd):
        key = (val, cwd)
        cached = rp_cache.get(key)
        if cached is not None:
            return cached
        res = orig_rule_path(val, cwd)
        rp_cache[key] = res
        return res

    def fast_arg_path(word, cwd):
        key = (word, cwd)
        cached = ap_cache.get(key)
        if cached is not None:
            return cached
        res = orig_arg_path(word, cwd)
        ap_cache[key] = res
        return res

    hg.long_path = fast_long_path
    hg.rule_path = fast_rule_path
    hg.arg_path = fast_arg_path
    try:
        return _evaluate_call_inner(
            hg, data, sessions, a, canonical, raw_name, raw_args, fmt, conv_id,
            agent_hint=agent_hint, raw_payload=raw_payload, tool_cwd=tool_cwd,
        )
    finally:
        hg.long_path = orig_long_path
        hg.rule_path = orig_rule_path
        hg.arg_path = orig_arg_path


def _evaluate_call_inner(hg, data, sessions, a, canonical, raw_name, raw_args, fmt, conv_id, agent_hint=None, raw_payload=None, tool_cwd=None):
    profile = a.profile
    if sessions is not None:
        profile = hg.session_profile(sessions, canonical, profile)
    if agent_hint and agent_hint.strip().lower() in ("dw-worker", "dw-blind-reviewer", "dw-visual-qa"):
        profile = "worker"

    probe_mode = bool(getattr(a, "dry_run_probe", False) or os.environ.get("AGYDW_DOCTOR_PROBE") == "1")
    audit_target = None if probe_mode else resolve_audit_path(a.audit, raw_payload, tool_cwd=tool_cwd)
    bridge_target = None if probe_mode else a.bridge_state
    pause_target = None if probe_mode else a.pause_flag

    if (canonical.get("tool_name") in ("Bash", "Monitor") and isinstance(canonical.get("tool_input"), dict)
            and isinstance(canonical["tool_input"].get("command"), str)):
        eval_call = dict(canonical, tool_input=dict(canonical["tool_input"], command=hg.strip_heredocs(canonical["tool_input"]["command"])))
    else:
        eval_call = canonical

    paused = hg.pause_state(a.pause_flag) if a.pause_flag and not (sessions is not None and profile == "director") else None
    if paused and not pause_allows_agy(hg, data, profile, eval_call, raw_name, raw_args):
        msg = f"PAUSED by {paused[0]} at {paused[1]}: {paused[2]}; {data['pause']['reason']}"
        hg.audit(audit_target, canonical, "pause", "PAUSE")
        if fmt == "antigravity":
            return emit_antigravity("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    tool_input = eval_call.get("tool_input") or {}
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    cwd = eval_call.get("cwd") if isinstance(eval_call.get("cwd"), str) else None
    home_rp = hg.rule_path(str(pathlib.Path.home()), None)
    shell = "powershell" if eval_call.get("tool_name") == "PowerShell" else "bash"
    is_shell = eval_call.get("tool_name") in hg.SHELL_TOOLS and isinstance(command, str)
    cmds = list(hg.simple_commands(hg.FD_DUP.sub(" ", command), shell)) if is_shell else []
    redir_targets = hg.redirect_targets(command, shell) if is_shell else []
    _, extra_writes, extra_ps = _extract_extra_shell_info(hg, cmds) if is_shell else ([], [], [])
    for ps_cmd in extra_ps:
        cmds.extend(hg.simple_commands(hg.FD_DUP.sub(" ", ps_cmd), "powershell"))
        redir_targets.extend(hg.redirect_targets(ps_cmd, "powershell"))

    sess_why = None
    if a.sessions and hg.base_profile(profile) != "director":
        sess_canon = hg.canonical(a.sessions)
        if eval_call.get("tool_name") in ("Write", "Edit") and isinstance(file_path, str) and file_path:
            cleaned_fp = _win_clean_path_str(hg.plain(file_path) or file_path)
            if hg.canonical(file_path, cwd) == sess_canon or hg.canonical(cleaned_fp, cwd) == sess_canon:
                sess_why = "sessions role map is managed by the Director/setup (docs/08); report BLOCKED instead"
        elif is_shell:
            s_glob = hg.rule_path(a.sessions, None)
            if hg.shell_write_match({"globs": [s_glob], "except": []}, cmds, redir_targets, cwd, home_rp, command) or any(
                hg.globs_match([s_glob], [], ap, home_rp) for w in extra_writes for ap in _arg_path_variants(hg, w, cwd)
            ):
                sess_why = "sessions role map is managed by the Director/setup (docs/08); report BLOCKED instead"

    protect_why = None
    if a.protect and hg.base_profile(profile) != "director":
        p_globs = _protect_target_globs(hg, a.protect)
        if eval_call.get("tool_name") in ("Write", "Edit") and isinstance(file_path, str) and file_path:
            protect_why = hg.protected_reason(file_path, cwd, a.protect)
            if not protect_why and any(hg.globs_match(p_globs, [], pv, home_rp) for pv in _rule_path_variants(hg, file_path, cwd)):
                protect_why = "director governance configs, state files, ledgers, reviews, audits, and active tools are protected (docs/02/08); report BLOCKED instead"
        elif is_shell:
            if hg.shell_write_match({"globs": p_globs, "except": []}, cmds, redir_targets, cwd, home_rp, command) or any(
                hg.globs_match(p_globs, [], ap, home_rp) for w in extra_writes for ap in _arg_path_variants(hg, w, cwd)
            ):
                protect_why = "work orders, DIRECTOR_LOG.md, and director governance files are protected from shell writes (docs/02); report BLOCKED instead"
            elif data is not None:
                protect_why = hg.protected_delete(data, cmds, cwd, p_globs)

    why = hg.reason(command, shell) if isinstance(command, str) else None
    if not why and extra_ps:
        for ps_cmd in extra_ps:
            why = hg.reason(ps_cmd, "powershell")
            if why:
                break
    label = "builtin"
    if not why and sessions is not None and is_shell:
        why = hg.protected_delete(data, cmds, cwd, sessions.get("protected", []))
        label = "protected" if why else label

    hits = hg.decide(data, profile, eval_call, a.rules) if data is not None else []
    hits += agy_extra_hits(
        hg, data, profile, eval_call, raw_name, a.rules, raw_args=raw_args,
        pre_cmds=list(cmds), pre_targets=list(redir_targets),
    )

    deny = hg.first(hits, "deny")
    ask = hg.first(hits, "ask")

    # Secret rules, G00 self-edit, and non-director G43 config/hooks protection are enforced regardless of --mode
    if a.mode != "block" and data is not None:
        kinds = {r.get("id"): r.get("category") for r in data["rules"]}
        hard = hg.first(
            [
                h for h in hits
                if h[0] == "deny" and (
                    kinds.get(h[1]) in hg.HARD_DENY
                    or h[1] in ("G00", "G53")
                    or (h[1] == "G43" and hg.base_profile(profile) != "director")
                )
            ],
            "deny",
        )
        if hard:
            msg = f"BLOCKED by agy_guard [{hard[1]}]: {hard[2]} (hard-denied in every mode)"
            hg.audit(audit_target, canonical, "deny", hard[1])
            if fmt == "antigravity":
                return emit_antigravity("deny", msg)
            print(msg, file=sys.stderr)
            return 2

    if sess_why or protect_why:
        msg = f"BLOCKED by agy_guard [protect]: {sess_why or protect_why}"
        hg.audit(audit_target, canonical, "deny", "protect")
        if fmt == "antigravity":
            return emit_antigravity("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    overwrite = maybe_utf8_overwrite(raw_name, raw_args) if (a.overwrite_utf8 and fmt == "antigravity") else None

    if a.mode != "block" and (deny or why or ask):
        rule, text = (deny[1], deny[2]) if deny else (label, why) if why else (ask[1], ask[2])
        if a.mode == "ask":
            hg.audit(audit_target, canonical, "ask", rule)
            if fmt == "antigravity":
                return emit_antigravity("force_ask", f"[{rule}] {text}", overwrite)
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                     "permissionDecisionReason": f"[{rule}] {text}"}}))
            return 0
        # remind mode (seat belt)
        line = f"hook_guard reminder [{rule}]: {text}"
        hg.audit(audit_target, canonical, "remind", rule)
        queue_bridge_alert(bridge_target, conv_id, line + hg.REMIND_NOTE)
        if fmt == "antigravity":
            return emit_antigravity("allow", line, overwrite)
        print(json.dumps({"systemMessage": line, "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "additionalContext": line + hg.REMIND_NOTE}}))
        return 0

    if deny or why:
        rule = deny[1] if deny else label
        msg = f"BLOCKED by agy_guard [{deny[1]}]: {deny[2]}" if deny else f"BLOCKED by agy_guard: {why}"
        hg.audit(audit_target, canonical, "deny", rule)
        conf = ((data.get("auto_pause") or {}).get(hg.base_profile(profile)) if pause_target and data is not None and not paused
                and not (sessions is not None and profile == "director") else None)
        paused_now = hg.auto_pause(pause_target, conf, rule) if conf else None
        if paused_now:
            msg += f"; AUTO-PAUSED: {paused_now}"
            hg.audit(audit_target, canonical, "pause", "AUTO")
        if fmt == "antigravity":
            return emit_antigravity("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    if ask:
        hg.audit(audit_target, canonical, "ask", ask[1])
        if fmt == "antigravity":
            return emit_antigravity("force_ask", f"[{ask[1]}] {ask[2]}", overwrite)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": f"[{ask[1]}] {ask[2]}"}}))
        return 0

    if a.loop_guard and audit_target:
        loop_mod = load_sibling("loop_guard")
        loop_conf = loop_mod.load_config(a.loop_config)
        found = loop_mod.hook_reminder(canonical, audit_target, loop_conf)
        if found:
            rule, text = f"LOOP:{found[0]}", found[1]
            hg.audit(audit_target, canonical, a.loop_mode, rule)
            if a.loop_mode == "ask":
                if fmt == "antigravity":
                    return emit_antigravity("force_ask", text, overwrite)
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                         "permissionDecisionReason": text}}))
                return 0
            queue_bridge_alert(bridge_target, conv_id, text)
            if fmt == "antigravity":
                return emit_antigravity("allow", text, overwrite)
            print(json.dumps({"systemMessage": text, "hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": text}}))
            return 0

    if fmt == "antigravity":
        return emit_antigravity("allow", None, overwrite)
    return 0


def _fail_closed(fmt_opt, reason):
    msg = f"BLOCKED by agy_guard [internal]: {reason}"
    if fmt_opt == "claude":
        print(msg, file=sys.stderr)
        return 2
    return emit_antigravity("deny", msg)


def _execute(raw_stdin, argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", default=str(HERE / "guard_rules.json"), help="rules JSON file (default: sibling guard_rules.json)")
    ap.add_argument("--profile", choices=("worker", "director", "cleaner"), default="worker", help="guard profile (default: worker)")
    ap.add_argument("--mode", choices=("remind", "ask", "block"), default="remind", help="remind (default), ask (force_ask), or block (deny)")
    ap.add_argument("--format", choices=("auto", "antigravity", "claude"), default="auto", help="input/output hook protocol (default: auto)")
    ap.add_argument("--shell", choices=("auto", "powershell", "bash"), default="auto", help="shell syntax for run_command (default: auto by OS)")
    ap.add_argument("--protect", action="append", default=[], help="DIRECTOR folder whose governance/ledger/state/tools writes are denied for non-director profiles")
    ap.add_argument("--pause-flag", help="pause flag file (pause_ctl.py on|off)")
    ap.add_argument("--sessions", help="optional sessions map JSON")
    ap.add_argument("--audit", help="hash-chained action log file for decision lines")
    ap.add_argument("--bridge-state", help="optional directory to queue reminder alerts for PreInvocation injection")
    ap.add_argument("--loop-guard", action="store_true", help="check --audit tail for stuck loops before clean calls")
    ap.add_argument("--loop-config", help="loop_guard JSON config")
    ap.add_argument("--loop-mode", choices=("remind", "ask"), default="remind", help="loop finding mode (default: remind)")
    ap.add_argument("--overwrite-utf8", action="store_true", help="opt-in: inject -X utf8 on python script commands via PreToolUse overwrite")
    ap.add_argument("--dry-run-probe", action="store_true", help="read-only health probe mode: skip writing audit, bridge-state, or pause flags")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    hg = load_sibling("hook_guard")
    try:
        data = hg.load_rules(a.rules) if a.rules else None
        sessions = hg.load_sessions(a.sessions) if a.sessions else None
    except hg.RulesError as error:
        if a.format != "claude":
            return emit_antigravity("deny", f"agy_guard configuration error: {error}")
        print(f"agy_guard: {error}", file=sys.stderr)
        return 1

    if not raw_stdin or not raw_stdin.strip():
        return _fail_closed(a.format, "empty hook stdin payload")
    try:
        payload = json.loads(raw_stdin)
    except ValueError as exc:
        return _fail_closed(a.format, f"malformed hook JSON input ({exc})")
    if not isinstance(payload, dict):
        return _fail_closed(a.format, "hook payload must be a JSON object")

    fmt = detect_format(payload, a.format)
    agent_hint = payload.get("agentName") or payload.get("subagentName")
    if fmt == "antigravity":
        if not isinstance(payload.get("toolCall"), dict):
            return _fail_closed(fmt, "PreToolUse payload missing valid toolCall")
        canonical, raw_name, raw_args = translate_antigravity_call(payload, a.shell)
        if not raw_name:
            return _fail_closed(fmt, "PreToolUse payload missing toolCall.name")
        conv_id = canonical.get("session_id")
        tool_cwd = first_str(raw_args, "Cwd", "cwd", "workspace") if isinstance(raw_args, dict) else None
    else:
        canonical = dict(payload)
        raw_name = canonical.get("tool_name") if isinstance(canonical.get("tool_name"), str) else ""
        if not raw_name:
            return _fail_closed(fmt, "hook payload missing tool_name")
        raw_args = canonical.get("tool_input") if isinstance(canonical.get("tool_input"), dict) else {}
        conv_id = canonical.get("session_id")
        tool_cwd = canonical.get("cwd") if isinstance(canonical.get("cwd"), str) else None

    return evaluate_call(
        hg, data, sessions, a, canonical, raw_name, raw_args, fmt, conv_id,
        agent_hint=agent_hint, raw_payload=payload, tool_cwd=tool_cwd,
    )


def run_from_raw(raw_stdin, argv=None, emit=True):
    """Run agy_guard cleanly in-process; returns (rc, stdout_str, stderr_str) when emit=False."""
    if emit:
        return _execute(raw_stdin, argv)
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf_out, buf_err
    try:
        rc = _execute(raw_stdin, argv)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return rc, buf_out.getvalue(), buf_err.getvalue()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw_stdin = sys.stdin.read() if sys.stdin is not None else ""
    return _execute(raw_stdin, argv)


if __name__ == "__main__":
    sys.exit(main())
