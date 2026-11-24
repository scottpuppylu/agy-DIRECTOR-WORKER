#!/usr/bin/env python3
"""Self-contained PreToolUse seatbelt entrypoint for plugin/director-work.

Runs inside an isolated standalone copy of `plugin/director-work/` without needing
`../../tools/` to exist. Only loads co-located `agy_guard.py` + `guard_rules.json`
in-process when this plugin lives inside a verified agydw distribution root (never
from `.agents/`, `.agent/`, `_agents/`, `_agent/`, or arbitrary workspace folders);
otherwise evaluates G00/G01/G02/G11/G21/G43/G50-G53 with stdlib only and fails closed.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
PLUGIN_DIR = HERE.parent
_GUARD_MOD_CACHE = {}

SECRET_PATH_RES = (
    re.compile(r"(?:^|/)\.env(?:\.[^/]+)?$", re.I),
    re.compile(r"\.(?:pem|key|p12|pfx)$", re.I),
    re.compile(r"(?:^|/)(?:id_rsa|id_ed25519)$", re.I),
    re.compile(r"(?:^|/)\.ssh(?:/|$)", re.I),
    re.compile(r"(?:^|/)\.aws/credentials$", re.I),
    re.compile(r"(?:^|/)(?:\.config/gh|GitHub CLI)/hosts\.yml$", re.I),
    re.compile(r"(?:^|/)\.(?:git-credentials|netrc)$", re.I),
    re.compile(r"(?:^|/)\.gemini/(?:.+/)?(?:oauth_creds\.json|[^/]*\.credentials\.json|accounts\.json)$", re.I),
)
SECRET_PATH_EXCEPT_RES = (
    re.compile(r"(?:^|/)\.env\.(?:example|sample|template|dist)$", re.I),
    re.compile(r"(?:^|/)\.ssh/known_hosts$", re.I),
    re.compile(r"\.pub$", re.I),
)
SECRET_CMD_RE = re.compile(
    r"(?:\b(?:cat|type|Get-Content|gc|more|less|head|tail|sed|awk|grep|rg|findstr|Select-String|sls|base64|cp|copy|Copy-Item|strings)\b"
    r".*?(?:\.ssh[/\\]|\.aws[/\\]credentials|\.env(?:\.[^/\s\"']+)?\b|id_rsa\b|id_ed25519\b|oauth_creds\.json|credentials\.json|accounts\.json)"
    r"|\bgit\s+show\s+\S+:[^\s'\"]*(?:\.env(?:\.[^\s'\"]+)?|\.pem|\.key|\.p12|\.pfx|id_rsa|id_ed25519|oauth_creds\.json|accounts\.json)"
    r"|\b(?:printenv|gh\s+auth\s+token)\b|(?:^|[;&|]\s*)env(?:\s*$|\s*[;|&])"
    r"|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY-----)",
    re.I,
)
SECRET_CMD_EXCEPT_RE = re.compile(
    r"(?:\.env\.(?:example|sample|template|dist)\b|\.ssh[/\\]known_hosts\b|[a-z0-9_.-]+\.pub\b)",
    re.I,
)
G01_FORCE_PUSH_RE = re.compile(
    r"\bgit\s+(?:-\S+(?:\s+[^\s-]+)?\s+)*push\b.*?(?:\s--force(?:-with-lease)?\b|\s-[a-zA-Z]*f\b|\s--mirror\b|\s--delete\b)",
    re.I,
)
G02_DESTRUCTIVE_RE = re.compile(
    r"\bgit\s+(?:-\S+(?:\s+[^\s-]+)?\s+)*(?:reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s|restore\s+--(?!staged)|push\b)",
    re.I,
)
CFG_PATH_RE = re.compile(
    r"(?:(?:^|/)\.agents(?:/|$)|(?:^|/)(?:AGENTS(?:\.local)?|GEMINI)\.md$|(?:^|/)\.gemini/(?:config/|antigravity-cli/settings\.json|settings\.json))",
    re.I,
)
SHELL_WRITE_VERBS_RE = re.compile(
    r"(?:>>?|\b(?:Set-Content|sc|Add-Content|ac|Out-File|Clear-Content|clc|New-Item|ni|tee|tee-object|mv|move|move-item|mi|ren|rename|rename-item|rni|cp|copy|copy-item|cpi|rm|del|erase|remove-item|ri|unlink|dd|sed|perl)\b|\bgit\s+(?:checkout|restore|rm|mv)\b)",
    re.I,
)
SELF_GUARD_NAMES = ("seatbelt_hook.py", "agy_guard.py", "hook_guard.py", "guard_rules.json", "director-work/hooks.json")
CFG_SHELL_NAMES = (".agents", "hooks.json", "plugins.json", "skills.json", "agents.md", "gemini.md", "agents.local.md")


def parse_args_dict(tc):
    raw = tc.get("args")
    if raw is None:
        raw = tc.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _is_inside_agents_dir(p):
    return any(part.lower() in (".agents", ".agent", "_agents", "_agent") for part in p.parts)


def find_full_guard(payload=None):
    """Locate (agy_guard.py, guard_rules.json) only inside a verified trusted distribution root."""
    if _is_inside_agents_dir(PLUGIN_DIR):
        return None, None
    dist_root = PLUGIN_DIR.parent.parent
    if not (dist_root / "VERSION").is_file() or not (dist_root / "templates" / "RESUME_STATE.md").is_file():
        return None, None
    folder = dist_root / "tools"
    g = folder / "agy_guard.py"
    r = folder / "guard_rules.json"
    if g.is_file() and r.is_file():
        return g, r
    return None, None


def _load_guard_module(guard_py):
    key = str(pathlib.Path(guard_py).resolve())
    mod = _GUARD_MOD_CACHE.get(key)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location("_agydw_delegated_guard", key)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {key}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _GUARD_MOD_CACHE[key] = mod
    return mod


def norm_path(val):
    return str(val).replace("\\", "/").strip()


def _clean_path_for_match(val):
    """Strip NTFS :stream suffixes and Windows trailing spaces/dots for consistent rule checks."""
    s = norm_path(val)
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


def _is_secret_path(np):
    if not np:
        return False
    for cand in {np, _clean_path_for_match(np)}:
        if any(rx.search(cand) for rx in SECRET_PATH_RES) and not any(ex.search(cand) for ex in SECRET_PATH_EXCEPT_RES):
            return True
    return False


def _is_secret_cmd(cmd):
    if not cmd or not SECRET_CMD_RE.search(cmd):
        return False
    scrubbed = SECRET_CMD_EXCEPT_RE.sub("", cmd)
    return bool(SECRET_CMD_RE.search(scrubbed))


def _collect_string_list(val):
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    if isinstance(val, list):
        return [str(x).strip() for x in val if isinstance(x, str) and x.strip()]
    return []


def _mcp_action_from_tool(name, args):
    low = (name or "").lower()
    if low == "call_mcp_tool":
        act = str(args.get("ToolName") or args.get("toolName") or "").strip()
    elif low.startswith("mcp__"):
        act = name.rsplit("__", 1)[-1]
    elif low.startswith("mcp_") and "_" in name[4:]:
        act = name[4:].split("_", 1)[1]
    else:
        return None
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", act)
    return re.sub(r"[^a-z0-9]+", "_", snake.lower()).strip("_")


def emit_agy(decision, reason=None):
    out = {"decision": decision}
    if reason:
        out["reason"] = reason
    print(json.dumps(out, ensure_ascii=False))
    return 0


def fallback_eval(payload, raw_stdin, profile, mode, fmt="auto"):
    is_agy = (fmt == "antigravity") or (fmt == "auto" and ("toolCall" in payload or "hookEventName" in payload or "workspacePaths" in payload))
    if is_agy:
        tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
        name = str(tc.get("name") or "")
        args = parse_args_dict(tc)
        low = name.lower()
        cmd = next((str(args[k]) for k in ("CommandLine", "command") if isinstance(args.get(k), str)), "")
        path = next(
            (norm_path(args[k]) for k in ("Path", "AbsolutePath", "TargetFile", "DirectoryPath", "SearchPath", "SearchDirectory", "file_path", "path") if isinstance(args.get(k), str) and args.get(k).strip()),
            "",
        )
        pat_candidates = []
        for k in ("SearchPath", "SearchDirectory", "Pattern", "pattern", "glob", "Includes"):
            pat_candidates.extend(norm_path(x) for x in _collect_string_list(args.get(k)))
        if path and pat_candidates:
            base = path.rstrip("/")
            combined = []
            for p_item in pat_candidates:
                if p_item == path:
                    continue
                combined.append(f"{base}/{p_item.lstrip('*/')}")
                combined.append(f"{base}/{p_item.lstrip('/')}")
            pat_candidates.extend(combined)
        read_candidates = ([path] if path else []) + pat_candidates
    else:
        name = str(payload.get("tool_name") or "")
        args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        low = name.lower()
        cmd = str(args.get("command") or "")
        path = next((norm_path(args[k]) for k in ("file_path", "path") if isinstance(args.get(k), str) and args.get(k).strip()), "")
        read_candidates = [norm_path(args[k]) for k in ("file_path", "path", "pattern", "glob") if isinstance(args.get(k), str) and args.get(k).strip()]

    write_tool = low in ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content", "write", "edit", "multiedit", "notebookedit")
    read_tool = low in ("read_file", "view_file", "list_dir", "grep_search", "find_by_name", "read", "grep", "glob")
    clean_p_low = _clean_path_for_match(path).lower() if path else ""
    cmd_low = cmd.lower() if cmd else ""
    shell_write = bool(cmd and SHELL_WRITE_VERBS_RE.search(cmd))

    hard_rule = None
    is_self_path = bool(
        write_tool
        and clean_p_low
        and (
            clean_p_low.endswith("scripts/seatbelt_hook.py")
            or clean_p_low.endswith("director-work/hooks.json")
            or clean_p_low in ("hooks.json", "guard_rules.json")
            or clean_p_low.endswith(("/guard_rules.json", "/agy_guard.py", "/hook_guard.py"))
            or ("director-work" in clean_p_low and clean_p_low.endswith("/hooks.json"))
        )
    )
    is_self_cmd = bool(shell_write and any(n in cmd_low for n in SELF_GUARD_NAMES))
    if is_self_path or is_self_cmd:
        hard_rule = ("G00", "hook guard scripts and hooks.json cannot be edited inside a guarded session (docs/08)")
    elif (read_tool and any(_is_secret_path(np) for np in read_candidates)) or _is_secret_cmd(cmd):
        hard_rule = ("G50", "secret or credential file access is denied in guarded sessions (docs/08)")
    elif profile != "director":
        is_cfg_write = bool(write_tool and path and (CFG_PATH_RE.search(path) or CFG_PATH_RE.search(_clean_path_for_match(path))))
        is_cfg_cmd = bool(shell_write and any(n in cmd_low for n in CFG_SHELL_NAMES))
        if is_cfg_write or is_cfg_cmd:
            hard_rule = ("G43", "Antigravity hooks, plugins and AGENTS.md/GEMINI.md rules are managed by PO/Director (docs/08)")

    if hard_rule:
        msg = f"BLOCKED by plugin seatbelt [{hard_rule[0]}]: {hard_rule[1]}"
        if is_agy:
            return emit_agy("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    soft_rule = None
    act = _mcp_action_from_tool(name, args)
    if act:
        ro = ("get", "list", "read", "search", "query", "status")
        words = act.split("_")
        if words[0] not in ro:
            if any(w in words for w in ("delete", "trash", "remove", "drop", "destroy", "purge", "wipe", "erase", "uninstall")):
                soft_rule = ("G11", "MCP delete/destroy operations require care or PO approval (docs/08)")
            elif any(w in words for w in ("buy", "purchase", "checkout", "payment", "pay", "subscribe", "transfer", "renew", "charge")):
                soft_rule = ("G30", "MCP paying/buying operations require PO approval (docs/08)")
            elif ("create_pr" in act or "create_pull_request" in act
                  or any(w in words for w in ("send", "post", "publish", "share", "reply", "comment", "merge", "deploy", "deployment", "promote", "upload", "release", "tweet"))):
                soft_rule = ("G21", "MCP sending/publishing operations require PO approval (docs/08)")
    if not soft_rule and cmd and G01_FORCE_PUSH_RE.search(cmd):
        soft_rule = ("G01", "force push, mirror push and deleting a remote ref rewrite shared history; only PO runs them (docs/08)")
    elif not soft_rule and cmd and G02_DESTRUCTIVE_RE.search(cmd):
        soft_rule = ("G02", "push, reset --hard, clean -f, and working-tree discard require PO approval or care (docs/08)")

    if soft_rule:
        code, why = soft_rule
        if mode == "block":
            msg = f"BLOCKED by plugin seatbelt [{code}]: {why}"
            if is_agy:
                return emit_agy("deny", msg)
            print(msg, file=sys.stderr)
            return 2
        if mode == "ask":
            if is_agy:
                return emit_agy("force_ask", f"[{code}] {why}")
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": f"[{code}] {why}"}}))
            return 0
        line = f"hook_guard reminder [{code}]: {why}"
        if is_agy:
            return emit_agy("allow", line)
        print(json.dumps({"systemMessage": line, "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": line}}))
        return 0

    return emit_agy("allow") if is_agy else 0


def _fail_closed(is_agy, reason):
    msg = f"BLOCKED by plugin seatbelt [internal]: {reason}"
    if is_agy:
        return emit_agy("deny", msg)
    print(msg, file=sys.stderr)
    return 2


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", choices=("worker", "director", "cleaner"), default="worker")
    ap.add_argument("--mode", choices=("remind", "ask", "block"), default="remind")
    ap.add_argument("--format", choices=("auto", "antigravity", "claude"), default="auto")
    a, extra = ap.parse_known_args(argv)

    is_agy_hint = (a.format != "claude")
    try:
        raw_stdin = sys.stdin.read() if sys.stdin is not None else ""
        if not raw_stdin.strip():
            return _fail_closed(is_agy_hint, "empty hook stdin payload")
        try:
            payload = json.loads(raw_stdin)
        except ValueError as exc:
            return _fail_closed(is_agy_hint, f"malformed hook JSON input ({exc})")
        if not isinstance(payload, dict):
            return _fail_closed(is_agy_hint, "hook payload must be a JSON object")

        is_agy = (a.format == "antigravity") or (
            a.format == "auto" and ("toolCall" in payload or "hookEventName" in payload or "workspacePaths" in payload)
        )
        guard_py, rules_json = find_full_guard(payload)
        if guard_py and rules_json:
            guard_mod = _load_guard_module(guard_py)
            guard_args = ["--rules", str(rules_json), "--profile", a.profile, "--mode", a.mode, "--format", a.format] + extra
            rc, out_str, err_str = guard_mod.run_from_raw(raw_stdin, guard_args, emit=False)
            if rc != 0:
                if not is_agy:
                    if out_str:
                        sys.stdout.write(out_str)
                    if err_str:
                        sys.stderr.write(err_str)
                    return rc
                if err_str:
                    sys.stderr.write(err_str)
                return _fail_closed(True, f"delegated agy_guard exited {rc}")
            if is_agy:
                try:
                    parsed = json.loads((out_str or "").strip())
                except ValueError:
                    parsed = None
                if not isinstance(parsed, dict) or not parsed.get("decision"):
                    return _fail_closed(True, "delegated agy_guard returned invalid JSON")
            if out_str:
                sys.stdout.write(out_str)
            if err_str:
                sys.stderr.write(err_str)
            return rc

        return fallback_eval(payload, raw_stdin, a.profile, a.mode, a.format)
    except Exception as exc:
        return _fail_closed(is_agy_hint, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
