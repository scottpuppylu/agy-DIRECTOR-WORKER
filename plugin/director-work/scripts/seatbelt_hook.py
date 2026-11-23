#!/usr/bin/env python3
"""Self-contained PreToolUse seatbelt entrypoint for plugin/director-work.

Runs inside an isolated standalone copy of `plugin/director-work/` without needing
`../../tools/` to exist. Only trusts full `agy_guard.py` + `guard_rules.json` when
co-located inside this plugin's own distribution (`<plugin_dir>/../../tools/`),
never loading scripts from workspace directories; otherwise evaluates core
G00/G01/G02/G43/G50-G53 rules with stdlib only and fails closed on errors.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
PLUGIN_DIR = HERE.parent

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
SECRET_CMD_RE = re.compile(
    r"(?:\b(?:cat|type|Get-Content|gc|more|less|head|tail|sed|awk)\b.*?(?:\.ssh/|\.aws/credentials|\.env(?:\.\S+)?\b|id_rsa\b|id_ed25519\b|oauth_creds\.json|credentials\.json|accounts\.json)"
    r"|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY-----)",
    re.I,
)
G01_FORCE_PUSH_RE = re.compile(r"\bgit\s+(?:.*\s+)?push\b.*?(?:\s--force(?:-with-lease)?\b|\s-[a-zA-Z]*f\b|\s--mirror\b|\s--delete\b)", re.I)
G02_DESTRUCTIVE_RE = re.compile(
    r"\bgit\s+(?:reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s|restore\s+--(?!staged)|push\b)",
    re.I,
)
CFG_PATH_RE = re.compile(
    r"(?:(?:^|/)\.agents/(?:hooks[^/]*\.json|plugins\.json|skills\.json|rules/)|(?:^|/)(?:AGENTS(?:\.local)?|GEMINI)\.md$|(?:^|/)\.gemini/(?:config/|antigravity-cli/settings\.json|settings\.json))",
    re.I,
)


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


def find_full_guard(payload=None):
    """Locate (agy_guard.py, guard_rules.json) strictly inside this plugin's own trusted parent tree."""
    folder = PLUGIN_DIR.parent.parent / "tools"
    g = folder / "agy_guard.py"
    r = folder / "guard_rules.json"
    if g.is_file() and r.is_file():
        return g, r
    return None, None


def norm_path(val):
    return str(val).replace("\\", "/").strip()


def _collect_string_list(val):
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    if isinstance(val, list):
        return [str(x).strip() for x in val if isinstance(x, str) and x.strip()]
    return []


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

    write_tool = low in ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content", "write", "edit")
    read_tool = low in ("read_file", "view_file", "list_dir", "grep_search", "find_by_name", "read", "grep", "glob")

    hard_rule = None
    if write_tool and path and (path.endswith("/scripts/seatbelt_hook.py") or path.endswith("/plugin/director-work/hooks.json") or path.endswith("/hooks.json") and "director-work" in path):
        hard_rule = ("G00", "hook guard scripts and hooks.json cannot be edited inside a guarded session (docs/08)")
    elif (read_tool and any(rx.search(np) for np in read_candidates for rx in SECRET_PATH_RES)) or (cmd and SECRET_CMD_RE.search(cmd)):
        hard_rule = ("G50", "secret or credential file access is denied in guarded sessions (docs/08)")

    if hard_rule:
        msg = f"BLOCKED by plugin seatbelt [{hard_rule[0]}]: {hard_rule[1]}"
        if is_agy:
            return emit_agy("deny", msg)
        print(msg, file=sys.stderr)
        return 2

    soft_rule = None
    if write_tool and path and profile != "director" and CFG_PATH_RE.search(path):
        soft_rule = ("G43", "Antigravity hooks, plugins and AGENTS.md/GEMINI.md rules are managed by PO/Director (docs/08)")
    elif cmd and G01_FORCE_PUSH_RE.search(cmd):
        soft_rule = ("G01", "force push, mirror push and deleting a remote ref rewrite shared history; only PO runs them (docs/08)")
    elif cmd and G02_DESTRUCTIVE_RE.search(cmd):
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
            a.format == "auto" and (not raw_stdin.strip() or "toolCall" in payload or "hookEventName" in payload or "workspacePaths" in payload)
        )
        guard_py, rules_json = find_full_guard(payload)
        if guard_py and rules_json:
            proc = subprocess.run(
                [sys.executable, "-B", "-I", "-X", "utf8", str(guard_py), "--rules", str(rules_json), "--profile", a.profile, "--mode", a.mode, "--format", a.format] + extra,
                input=raw_stdin,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=12,
            )
            if proc.returncode != 0:
                if not is_agy:
                    if proc.stdout:
                        sys.stdout.write(proc.stdout)
                    if proc.stderr:
                        sys.stderr.write(proc.stderr)
                    return proc.returncode
                if proc.stderr:
                    sys.stderr.write(proc.stderr)
                return _fail_closed(True, f"delegated agy_guard exited {proc.returncode}")
            if is_agy:
                try:
                    parsed = json.loads((proc.stdout or "").strip())
                except ValueError:
                    parsed = None
                if not isinstance(parsed, dict) or not parsed.get("decision"):
                    return _fail_closed(True, "delegated agy_guard returned invalid JSON")
            if proc.stdout:
                sys.stdout.write(proc.stdout)
            if proc.stderr:
                sys.stderr.write(proc.stderr)
            return proc.returncode

        return fallback_eval(payload, raw_stdin, a.profile, a.mode, a.format)
    except Exception as exc:
        return _fail_closed(is_agy_hint, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
