#!/usr/bin/env python3
"""Antigravity PreToolUse guard (dual-protocol: Antigravity camelCase + Claude snake_case).

Wraps upstream tools/hook_guard.py and tools/guard_rules.json without modifying them, translating
Antigravity tool names and arguments (both App 2.0 and IDE/CLI naming sets) into hook_guard's
canonical rules engine and emitting Antigravity PreToolUse JSON decisions:
  allow       clean call, or remind-mode warning (with reason + optional bridge-state reminder)
  force_ask   flagged in --mode ask (prompts user, ignoring cached Always-Allow grants)
  deny        hard blocks (G00 self-edit, G50-G53 secrets/credentials, worker G40/G43 config
              edits, --protect DIRECTOR/orders & DIRECTOR_LOG.md, --pause-flag, or --mode block)

In Antigravity format, stdout always receives a JSON object and exit code is 0 (so Antigravity
reads the JSON decision rather than treating the hook itself as crashed). In Claude format,
behavior matches hook_guard.py (exit 2 + stderr on deny).

Usage:
  python -I -X utf8 agy_guard.py --rules tools/guard_rules.json --profile worker --mode remind
"""
import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent

AGY_WRITE_TOOLS = ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content")
AGY_READ_TOOLS = ("read_file", "view_file", "list_dir")
AGY_SEARCH_TOOLS = ("grep_search", "find_by_name")
AGY_INTERNAL_REPORT_TOOLS = ("send_message", "post_message", "SendMessage", "mcp__ccd_session_mgmt__send_message")

# Antigravity-specific config/rule files (same protection policy as G40 for Claude Code files)
AGY_CONFIG_GLOBS = [
    "**/.agents/hooks*.json",
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

AGY_PROTECTED_FILES = ("agy_guard.py", "agy_hook_bridge.py", "agy_audit.py", "hook_guard.py", "guard_rules.json")


def load_sibling(name):
    """Load a sibling module by file name without polluting sys.path."""
    spec = importlib.util.spec_from_file_location(f"_agy_sib_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def translate_antigravity_call(payload, shell_opt="auto"):
    """Translate an Antigravity PreToolUse stdin payload into canonical hook_guard dict + metadata."""
    tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    raw_name = tc.get("name") if isinstance(tc.get("name"), str) else ""
    raw_args = tc.get("args") if tc.get("args") is not None else tc.get("arguments")
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except ValueError:
            raw_args = {}
    args = raw_args if isinstance(raw_args, dict) else {}
    ws = payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
    cwd = first_str(args, "Cwd", "cwd") or (payload.get("cwd") if isinstance(payload.get("cwd"), str) else None) or (ws[0] if ws and isinstance(ws[0], str) else None)
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
        path = first_str(args, "Path", "AbsolutePath", "DirectoryPath", "file_path")
        canonical["tool_input"] = {"file_path": path} if path else {}
    elif low in ("write_file", "write_to_file"):
        canonical["tool_name"] = "Write"
        path = first_str(args, "Path", "TargetFile", "file_path")
        canonical["tool_input"] = {"file_path": path} if path else {}
    elif low in ("edit_file", "replace_file_content", "multi_replace_file_content"):
        canonical["tool_name"] = "Edit"
        path = first_str(args, "Path", "TargetFile", "file_path")
        canonical["tool_input"] = {"file_path": path} if path else {}
    elif low == "grep_search":
        canonical["tool_name"] = "Grep"
        path = first_str(args, "SearchPath", "path")
        canonical["tool_input"] = {k: v for k, v in (("path", path), ("glob", args.get("Query"))) if v}
    elif low == "find_by_name":
        canonical["tool_name"] = "Glob"
        path = first_str(args, "SearchDirectory", "path")
        canonical["tool_input"] = {k: v for k, v in (("path", path), ("pattern", args.get("Pattern"))) if v}
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


def agy_extra_hits(hg, data, profile, canonical, raw_name, rules_path):
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
    commands = list(hg.simple_commands(hg.FD_DUP.sub(" ", command), shell)) if shell_call else []
    targets = hg.redirect_targets(command, shell) if shell_call else []

    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    target = hg.rule_path(file_path, cwd) if isinstance(file_path, str) and file_path else None

    # Protect agy_guard.py / agy_hook_bridge.py / agy_audit.py alongside G00
    protected_scripts = {hg.rule_path(str(HERE / name), None) for name in AGY_PROTECTED_FILES}
    if rules_path:
        protected_scripts.add(hg.rule_path(str(rules_path), None))
    names = {n.lower() for n in AGY_PROTECTED_FILES}
    if rules_path:
        names.add(pathlib.Path(str(rules_path)).name.lower())
    if (tool in hg.WRITE_TOOLS and target in protected_scripts) or (shell_call and hg.self_write(commands, names, targets)):
        hits.append((hg.SELF_RULE["decision"][base_prof], "G00", hg.SELF_RULE["reason"]))

    # G43 (Antigravity config/rule files protection, analogous to G40)
    cfg_decision = "deny" if base_prof == "worker" else "ask"
    cfg_reason = "Antigravity hooks, plugins and AGENTS.md/GEMINI.md rules are managed by the PO or Director (docs/08); report the change you need"
    if tool in hg.WRITE_TOOLS and target and hg.globs_match(AGY_CONFIG_GLOBS, [], target, home):
        hits.append((cfg_decision, "G43", cfg_reason))
    elif shell_call and hg.shell_write_match({"globs": AGY_CONFIG_GLOBS, "except": []}, commands, targets, cwd, home, command):
        hits.append((cfg_decision, "G43", cfg_reason))

    # G53 (Antigravity / Gemini OAuth & account credential files)
    sec_reason = "credential files are not read in a session (docs/08); report what you need to the Director"
    if tool in ("Read", "Grep", "Glob") and target and hg.globs_match(AGY_EXTRA_SECRET_GLOBS, [], target, home):
        hits.append(("deny", "G53", sec_reason))
    elif shell_call:
        for _, rest in commands:
            for word in rest:
                if not word.startswith("-") and hg.globs_match(AGY_EXTRA_SECRET_GLOBS, [], hg.arg_path(word, cwd), home):
                    hits.append(("deny", "G53", sec_reason))
                    break

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
    """If opt-in --overwrite-utf8 is enabled, inject '-I -X utf8' on bare python/py script invocations."""
    if raw_name.lower() != "run_command":
        return None
    cmd = raw_args.get("CommandLine")
    if not isinstance(cmd, str):
        return None
    m = re.match(r'^(\s*)(python(?:3(?:\.\d+)?)?|py)(\s+)(?!.*(?:-X\s*utf8|-I\b))([^\s;|&]+\.py\b.*)$', cmd, re.I)
    if not m:
        return None
    new_cmd = f"{m.group(1)}{m.group(2)}{m.group(3)}-I -X utf8 {m.group(4)}"
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


def evaluate_call(hg, data, sessions, a, canonical, raw_name, raw_args, fmt, conv_id, agent_hint=None):
    """Evaluate one translated call using hook_guard rules + Antigravity extras."""
    profile = a.profile
    if sessions is not None:
        profile = hg.session_profile(sessions, canonical, profile)
    if agent_hint and agent_hint.strip().lower() in ("dw-worker", "dw-blind-reviewer", "dw-visual-qa"):
        profile = "worker"

    if (canonical.get("tool_name") in ("Bash", "Monitor") and isinstance(canonical.get("tool_input"), dict)
            and isinstance(canonical["tool_input"].get("command"), str)):
        eval_call = dict(canonical, tool_input=dict(canonical["tool_input"], command=hg.strip_heredocs(canonical["tool_input"]["command"])))
    else:
        eval_call = canonical

    paused = hg.pause_state(a.pause_flag) if a.pause_flag and not (sessions is not None and profile == "director") else None
    if paused and not pause_allows_agy(hg, data, profile, eval_call, raw_name, raw_args):
        msg = f"PAUSED by {paused[0]} at {paused[1]}: {paused[2]}; {data['pause']['reason']}"
        hg.audit(a.audit, canonical, "pause", "PAUSE")
        if fmt == "antigravity":
            return emit_antigravity("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    tool_input = eval_call.get("tool_input") or {}
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    cwd = eval_call.get("cwd") if isinstance(eval_call.get("cwd"), str) else None

    sess_why = None
    if a.sessions and hg.base_profile(profile) != "director":
        sess_canon = hg.canonical(a.sessions)
        if eval_call.get("tool_name") in ("Write", "Edit") and isinstance(file_path, str) and file_path:
            if hg.canonical(file_path, cwd) == sess_canon:
                sess_why = "sessions role map is managed by the Director/setup (docs/08); report BLOCKED instead"
        elif eval_call.get("tool_name") in hg.SHELL_TOOLS and isinstance(command, str):
            shell = "powershell" if eval_call.get("tool_name") == "PowerShell" else "bash"
            s_glob = hg.rule_path(a.sessions, None)
            home_rp = hg.rule_path(str(pathlib.Path.home()), None)
            cmds = list(hg.simple_commands(hg.FD_DUP.sub(" ", command), shell))
            if hg.shell_write_match({"globs": [s_glob], "except": []}, cmds, hg.redirect_targets(command, shell), cwd, home_rp, command):
                sess_why = "sessions role map is managed by the Director/setup (docs/08); report BLOCKED instead"

    if a.protect and hg.base_profile(profile) != "director" and eval_call.get("tool_name") in ("Write", "Edit") and isinstance(file_path, str) and file_path:
        why = hg.protected_reason(file_path, cwd, a.protect)
    elif isinstance(command, str):
        why = hg.reason(command, "powershell" if eval_call.get("tool_name") == "PowerShell" else "bash")
    else:
        why = None

    label = "builtin"
    if not why and sessions is not None and isinstance(command, str) and eval_call.get("tool_name") in hg.SHELL_TOOLS:
        shell = "powershell" if eval_call.get("tool_name") == "PowerShell" else "bash"
        why = hg.protected_delete(data, list(hg.simple_commands(hg.FD_DUP.sub(" ", command), shell)), cwd, sessions.get("protected", []))
        label = "protected" if why else label

    hits = hg.decide(data, profile, eval_call, a.rules) if data is not None else []
    hits += agy_extra_hits(hg, data, profile, eval_call, raw_name, a.rules)

    deny = hg.first(hits, "deny")
    ask = hg.first(hits, "ask")

    # Secret rules and G00/protected are enforced regardless of --mode
    if a.mode != "block" and data is not None:
        kinds = {r.get("id"): r.get("category") for r in data["rules"]}
        hard = hg.first([h for h in hits if h[0] == "deny" and (kinds.get(h[1]) in hg.HARD_DENY or h[1] in ("G00", "G53"))], "deny")
        if hard:
            msg = f"BLOCKED by agy_guard [{hard[1]}]: {hard[2]} (hard-denied in every mode)"
            hg.audit(a.audit, canonical, "deny", hard[1])
            if fmt == "antigravity":
                return emit_antigravity("deny", msg)
            print(msg, file=sys.stderr)
            return 2

    if sess_why or (a.protect and hg.base_profile(profile) != "director" and why and eval_call.get("tool_name") in ("Write", "Edit")):
        # Protecting DIRECTOR/orders, DIRECTOR_LOG.md, and .sessions.json from non-director writes is always hard-denied
        msg = f"BLOCKED by agy_guard [protect]: {sess_why or why}"
        hg.audit(a.audit, canonical, "deny", "protect")
        if fmt == "antigravity":
            return emit_antigravity("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    overwrite = maybe_utf8_overwrite(raw_name, raw_args) if (a.overwrite_utf8 and fmt == "antigravity") else None

    if a.mode != "block" and (deny or why or ask):
        rule, text = (deny[1], deny[2]) if deny else (label, why) if why else (ask[1], ask[2])
        if a.mode == "ask":
            hg.audit(a.audit, canonical, "ask", rule)
            if fmt == "antigravity":
                return emit_antigravity("force_ask", f"[{rule}] {text}", overwrite)
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                     "permissionDecisionReason": f"[{rule}] {text}"}}))
            return 0
        # remind mode (seat belt)
        line = f"hook_guard reminder [{rule}]: {text}"
        hg.audit(a.audit, canonical, "remind", rule)
        queue_bridge_alert(a.bridge_state, conv_id, line + hg.REMIND_NOTE)
        if fmt == "antigravity":
            return emit_antigravity("allow", line, overwrite)
        print(json.dumps({"systemMessage": line, "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "additionalContext": line + hg.REMIND_NOTE}}))
        return 0

    if deny or why:
        rule = deny[1] if deny else label
        msg = f"BLOCKED by agy_guard [{deny[1]}]: {deny[2]}" if deny else f"BLOCKED by agy_guard: {why}"
        hg.audit(a.audit, canonical, "deny", rule)
        conf = ((data.get("auto_pause") or {}).get(hg.base_profile(profile)) if a.pause_flag and data is not None and not paused
                and not (sessions is not None and profile == "director") else None)
        paused_now = hg.auto_pause(a.pause_flag, conf, rule) if conf else None
        if paused_now:
            msg += f"; AUTO-PAUSED: {paused_now}"
            hg.audit(a.audit, canonical, "pause", "AUTO")
        if fmt == "antigravity":
            return emit_antigravity("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    if ask:
        hg.audit(a.audit, canonical, "ask", ask[1])
        if fmt == "antigravity":
            return emit_antigravity("force_ask", f"[{ask[1]}] {ask[2]}", overwrite)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": f"[{ask[1]}] {ask[2]}"}}))
        return 0

    if a.loop_guard and a.audit:
        loop_mod = load_sibling("loop_guard")
        loop_conf = loop_mod.load_config(a.loop_config)
        found = loop_mod.hook_reminder(canonical, a.audit, loop_conf)
        if found:
            rule, text = f"LOOP:{found[0]}", found[1]
            hg.audit(a.audit, canonical, a.loop_mode, rule)
            if a.loop_mode == "ask":
                if fmt == "antigravity":
                    return emit_antigravity("force_ask", text, overwrite)
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                         "permissionDecisionReason": text}}))
                return 0
            queue_bridge_alert(a.bridge_state, conv_id, text)
            if fmt == "antigravity":
                return emit_antigravity("allow", text, overwrite)
            print(json.dumps({"systemMessage": text, "hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": text}}))
            return 0

    if fmt == "antigravity":
        return emit_antigravity("allow", None, overwrite)
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", default=str(HERE / "guard_rules.json"), help="rules JSON file (default: sibling guard_rules.json)")
    ap.add_argument("--profile", choices=("worker", "director", "cleaner"), default="worker", help="guard profile (default: worker)")
    ap.add_argument("--mode", choices=("remind", "ask", "block"), default="remind", help="remind (default), ask (force_ask), or block (deny)")
    ap.add_argument("--format", choices=("auto", "antigravity", "claude"), default="auto", help="input/output hook protocol (default: auto)")
    ap.add_argument("--shell", choices=("auto", "powershell", "bash"), default="auto", help="shell syntax for run_command (default: auto by OS)")
    ap.add_argument("--protect", action="append", default=[], help="DIRECTOR folder whose orders/ and DIRECTOR_LOG.md writes are denied for non-director profiles")
    ap.add_argument("--pause-flag", help="pause flag file (pause_ctl.py on|off)")
    ap.add_argument("--sessions", help="optional sessions map JSON")
    ap.add_argument("--audit", help="hash-chained action log file for decision lines")
    ap.add_argument("--bridge-state", help="optional directory to queue reminder alerts for PreInvocation injection")
    ap.add_argument("--loop-guard", action="store_true", help="check --audit tail for stuck loops before clean calls")
    ap.add_argument("--loop-config", help="loop_guard JSON config")
    ap.add_argument("--loop-mode", choices=("remind", "ask"), default="remind", help="loop finding mode (default: remind)")
    ap.add_argument("--overwrite-utf8", action="store_true", help="opt-in: inject -I -X utf8 on bare python script commands via PreToolUse overwrite")
    a = ap.parse_args(argv)

    hg = load_sibling("hook_guard")
    try:
        data = hg.load_rules(a.rules) if a.rules else None
        sessions = hg.load_sessions(a.sessions) if a.sessions else None
    except hg.RulesError as error:
        if a.format == "antigravity":
            return emit_antigravity("deny", f"agy_guard configuration error: {error}")
        print(f"agy_guard: {error}", file=sys.stderr)
        return 1

    raw_stdin = sys.stdin.read() if sys.stdin is not None else ""
    try:
        payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except ValueError:
        return emit_antigravity("allow") if a.format == "antigravity" else 0
    if not isinstance(payload, dict):
        return emit_antigravity("allow") if a.format == "antigravity" else 0

    fmt = detect_format(payload, a.format)
    agent_hint = payload.get("agentName") or payload.get("subagentName")
    if fmt == "antigravity":
        canonical, raw_name, raw_args = translate_antigravity_call(payload, a.shell)
        conv_id = canonical.get("session_id")
    else:
        canonical = dict(payload)
        raw_name = canonical.get("tool_name") if isinstance(canonical.get("tool_name"), str) else ""
        raw_args = canonical.get("tool_input") if isinstance(canonical.get("tool_input"), dict) else {}
        conv_id = canonical.get("session_id")

    return evaluate_call(hg, data, sessions, a, canonical, raw_name, raw_args, fmt, conv_id, agent_hint=agent_hint)


if __name__ == "__main__":
    sys.exit(main())
