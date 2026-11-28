#!/usr/bin/env python3
"""Antigravity plugin PreToolUse seatbelt hook for director-work (3.0.0-agy.7).

Fail-closed self-contained seatbelt that delegates to `<ADAPTER>/agy_guard.py` +
`<ROOT>/tools/guard_rules.json` when available outside `.agents/`, and falls back
to standalone evaluation (secrets G50/G53, G43 config/rules, `--protect` DIRECTOR,
G01/G02 shell guardrails) when installed standalone.
"""
import io
import json
import os
import pathlib
import re
import sys

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent
ADAPTER_DIR = PLUGIN_DIR.parent.parent
ROOT_DIR = ADAPTER_DIR.parent.parent

SHELL_TOOL_NAMES = {"run_command", "bash", "powershell", "monitor"}
WRITE_TOOL_NAMES = {"write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content", "Write", "Edit", "MultiEdit"}
READ_TOOL_NAMES = {"read_file", "view_file", "view_file_outline", "view_code_item", "list_dir", "grep_search", "find_by_name", "Read", "Grep", "Glob"}

CRED_PATH_RE = re.compile(
    r"(?:^|[/\\\s'\"@=:])(?:\.env(?:\.(?!example\b|sample\b|template\b|dist\b)[\w.-]+)?|\.envrc|id_(?:rsa|ecdsa|ed25519)[\w.-]*|"
    r"oauth_creds\.json|credentials\.json|\.git-credentials|\.npmrc|\.pypirc|\.netrc|_netrc)(?:$|[/\\\s'\";&|)])|"
    r"(?:^|[/\\\s'\"@=:])\.(?:ssh|aws|gemini)[/\\].*(?:key|cred|oauth|id_|secret)|"
    r"\.(?:pem|key|p12|pfx)(?:$|[/\\\s'\";&|)])",
    re.I,
)
AGY_CONFIG_RE = re.compile(
    r"(?:^|[/\\])(?:\.agents[/\\](?:hooks[^/\\]*\.json|plugins\.json)|\.agent[/\\]rules[/\\]|"
    r"plugin[/\\]director-work[/\\]hooks\.json|seatbelt_hook\.py|"
    r"\.gemini[/\\](?:antigravity|config)[/\\])",
    re.I,
)
DIRECTOR_PROT_RE = re.compile(
    r"(?:^|[/\\])DIRECTOR(?:-[^/\\]+)?[/\\](?:DIRECTOR_LOG\.md|DIRECTOR_STATE\.md|PO_PENDING\.md|"
    r"director_work\.json|\.sessions\.json|\.agydw_setup_state\.json|orders[/\\]|tools[/\\]|adapters[/\\]|brief-history[/\\])",
    re.I,
)
DANGEROUS_GIT_RE = re.compile(
    r"\bgit\b(?:\s+-\w+(?:\s+\S+)?)*\s+(push(?:\s|$)|reset\s+--hard\b|clean\b(?![^;&|]*(?:-n\b|--dry-run\b)))",
    re.I,
)
SUB_CID_UNWRAP = re.compile(r'^"(.*)"$', re.S)


def _is_inside_agents_dir(p):
    parts = {part.lower() for part in p.resolve().parts}
    return bool(parts & {".agents", ".agent", "_agents", "_agent"})


def find_full_guard():
    """Return `(guard_py, rules_json)` when running from rules repo or `DIRECTOR/adapters/antigravity/`."""
    guard_py = ADAPTER_DIR / "agy_guard.py"
    rules_json = ROOT_DIR / "tools" / "guard_rules.json"
    if _is_inside_agents_dir(PLUGIN_DIR):
        return None
    if not guard_py.is_file() or not rules_json.is_file():
        return None
    has_marker = any((ROOT_DIR / m).is_file() for m in ("VERSION", "tools/INSTALL_MANIFEST.json", "RESUME_STATE.md", "templates/RESUME_STATE.md"))
    if not has_marker and not (ADAPTER_DIR / "AGYDW_ADAPTER_MANIFEST.json").is_file():
        return None
    return guard_py, rules_json


def _unwrap(val):
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
            try:
                dec = json.loads(s)
                if isinstance(dec, (str, int, float, bool, list, dict)) or dec is None:
                    return dec
            except ValueError:
                pass
    return val


def _extract_args(tc):
    for key in ("args", "arguments", "tool_input", "input"):
        raw = tc.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        if isinstance(raw, dict):
            return {k: _unwrap(v) for k, v in raw.items()}
    return {}


def _delegate_in_process(guard_py, rules_json, raw_stdin, mode):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agydw_plug_guard", guard_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    argv = ["--rules", str(rules_json), "--mode", mode]
    old_in, old_out = sys.stdin, sys.stdout
    buf = io.StringIO()
    try:
        sys.stdin = io.StringIO(raw_stdin)
        sys.stdout = buf
        rc = mod.main(argv)
    finally:
        sys.stdin = old_in
        sys.stdout = old_out
    return rc, buf.getvalue()


def fallback_eval(raw_stdin, mode="remind"):
    """Fail-closed standalone seatbelt evaluator when full `agy_guard.py` is absent."""
    if not raw_stdin or not raw_stdin.strip():
        return {"decision": "deny", "reason": "seatbelt_hook: empty input (fail-closed)"}
    try:
        payload = json.loads(raw_stdin)
    except ValueError as exc:
        return {"decision": "deny", "reason": f"seatbelt_hook: malformed JSON ({exc}); fail-closed"}
    if not isinstance(payload, dict):
        return {"decision": "deny", "reason": "seatbelt_hook: input must be JSON object (fail-closed)"}

    tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool = str(tc.get("name") or payload.get("tool_name") or "").strip()
    if not tool:
        return {"decision": "deny", "reason": "seatbelt_hook: missing tool name (fail-closed)"}
    args = _extract_args(tc) if tc else ({k: _unwrap(v) for k, v in (payload.get("tool_input") or {}).items()} if isinstance(payload.get("tool_input"), dict) else {})
    low = tool.lower()

    path_candidates = [str(args[k]) for k in ("Path", "TargetFile", "AbsolutePath", "file_path", "path", "SearchPath", "SearchDirectory", "DirectoryPath") if isinstance(args.get(k), str) and args[k].strip()]
    if low == "find_by_name" and args.get("Pattern") and (args.get("SearchDirectory") or payload.get("cwd")):
        path_candidates.append(str(pathlib.Path(str(args.get("SearchDirectory") or payload.get("cwd"))) / str(args["Pattern"])))

    if low in READ_TOOL_NAMES or low in WRITE_TOOL_NAMES:
        for p in path_candidates:
            p_norm = p.replace("\\", "/")
            if CRED_PATH_RE.search(p_norm):
                return {"decision": "deny", "reason": f"BLOCKED by seatbelt [G50/G53]: credential read/write `{p}` is forbidden"}

    if low in WRITE_TOOL_NAMES:
        for p in path_candidates:
            p_norm = p.replace("\\", "/")
            if AGY_CONFIG_RE.search(p_norm):
                return {"decision": "deny", "reason": f"BLOCKED by seatbelt [G43]: Antigravity config/rules `{p}` protected"}
            if DIRECTOR_PROT_RE.search(p_norm):
                return {"decision": "deny", "reason": f"BLOCKED by seatbelt [PROTECT]: DIRECTOR governance file `{p}` protected"}

    if low in SHELL_TOOL_NAMES:
        cmd = str(args.get("CommandLine") or args.get("command") or "")
        cmd_norm = cmd.replace("\\", "/")
        if CRED_PATH_RE.search(cmd_norm):
            has_reader = re.search(
                r"\b(?:cat|tac|nl|head|tail|more|less|bat|grep|egrep|fgrep|rg|findstr|"
                r"select-string|sls|get-content|gc|type|cp|copy|copy-item|cpi|mv|move|"
                r"move-item|mi|scp|rsync|base64|certutil|xxd|od|hexdump|strings|"
                r"format-hex|fhx|import-csv|get-filehash|curl|wget|invoke-webrequest|iwr|"
                r"invoke-restmethod|irm|tar|zip|gzip|jq|yq|tr|cut|sort|uniq|wc|diff|cmp|"
                r"comm|paste|tee|dd|openssl|python|python3|py|node|perl|ruby|"
                r"pbcopy|clip|xclip|xsel|set-clipboard|scb)\b",
                cmd,
                re.I,
            ) or re.search(r"(?:^|[\s;|&(])0?<\s*", cmd)
            if has_reader:
                rid = "G53" if re.search(r"oauth_creds|\.gemini[/\\]", cmd_norm, re.I) else "G50"
                return {"decision": "deny", "reason": f"BLOCKED by seatbelt [{rid}]: shell read of credential path is forbidden"}
        m_git = DANGEROUS_GIT_RE.search(cmd)
        if m_git:
            rid = "G01" if "--force" in cmd else "G02"
            if mode == "block":
                return {"decision": "deny", "reason": f"BLOCKED by seatbelt [{rid}]: `{m_git.group(1).strip()}` requires PO approval"}
            if mode == "ask":
                return {"decision": "force_ask", "reason": f"[{rid}] `{m_git.group(1).strip()}` requires PO approval"}
            return {"decision": "allow", "reason": f"seatbelt [{rid}]: `{m_git.group(1).strip()}` requires PO approval (docs/08)"}

    if low.startswith("mcp"):
        if any(w in low for w in ("delete", "trash", "destroy", "purge", "wipe")):
            return {"decision": "deny" if mode == "block" else ("force_ask" if mode == "ask" else "allow"), "reason": f"[{ 'G11' }]: destructive MCP action `{tool}`"}
        if any(w in low for w in ("buy", "purchase", "checkout", "payment", "pay")):
            return {"decision": "deny", "reason": f"BLOCKED by seatbelt [G30]: payment MCP `{tool}` requires PO"}

    return {"decision": "allow"}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    mode = "remind"
    if "--mode" in (argv or sys.argv[1:]):
        args_l = list(argv or sys.argv[1:])
        idx = args_l.index("--mode")
        if idx + 1 < len(args_l) and args_l[idx + 1] in ("remind", "ask", "block"):
            mode = args_l[idx + 1]
    raw_stdin = sys.stdin.read() if sys.stdin is not None else ""
    found = find_full_guard()
    if found is not None:
        guard_py, rules_json = found
        try:
            rc, out = _delegate_in_process(guard_py, rules_json, raw_stdin, mode)
            if rc == 0 and out.strip():
                sys.stdout.write(out if out.endswith("\n") else out + "\n")
                return 0
        except Exception:
            pass
    res = fallback_eval(raw_stdin, mode=mode)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
