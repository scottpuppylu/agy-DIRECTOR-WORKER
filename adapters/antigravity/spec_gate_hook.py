#!/usr/bin/env python3
"""Antigravity PreToolUse Spec Gate hook (3.0.0-agy.7, item R6). Fail-closed.

Blocks product-code edits, branches, worktrees, non-read-only commands, mutating MCP
calls, and coding subagent dispatch until `DIRECTOR/BRIEF.md` passes `spec_gate.py`.
Always blocks self-approval (`spec_gate.py approve`, `gate_check.py --snapshot`) and
writes to hook/tool/adapter files even after the brief passes.

Never writes or trusts a pass cache: checks `BRIEF.md` and `brief-history/` live on
every non-read-only call using frozen `tools/spec_gate.py` and `tools/hook_guard.py`.
"""
import argparse
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agy_spec_gate_common", HERE / "agy_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()

SAFE_REDIRECT_TARGETS = {"/dev/null", "nul", "$null"}
READONLY_SHELL_CMDS = {
    "ls", "dir", "cat", "tac", "nl", "head", "tail", "more", "less", "bat",
    "wc", "grep", "egrep", "fgrep", "rg", "find", "which", "where", "command",
    "pwd", "echo", "echo.", "printf", "true", "false", "test", "[",
    "get-childitem", "gci", "get-content", "gc", "select-string", "sls",
    "get-location", "gl", "write-output", "select-object", "select",
    "sort-object", "sort", "where-object", "measure-object", "format-table",
    "ft", "format-list", "fl", "findstr", "type", "tree", "stat", "file",
    "readlink", "realpath", "basename", "dirname",
}
SAFE_FIND_FLAGS = {"-name", "-iname", "-path", "-ipath", "-type", "-maxdepth", "-mindepth", "-print", "-print0"}
SAFE_GIT_BRANCH_FLAGS = {
    "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "-l",
    "--contains", "--no-contains", "--merged", "--no-merged", "--show-current",
}
GIT_BRANCH_VALUE_FLAGS = {"--contains", "--no-contains", "--merged", "--no-merged", "--sort", "--format"}
DANGEROUS_ENV_VARS = {
    "GIT_EXTERNAL_DIFF", "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND",
    "GIT_TEMPLATE_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "PYTHONPATH", "PYTHONSTARTUP", "PERL5OPT", "NODE_OPTIONS", "IFS", "PATH",
}
READONLY_PYTHON_BASES = {"python", "python3", "py"}
MUTATING_MCP_VERBS = {
    "write", "edit", "create", "update", "delete", "trash", "destroy", "purge",
    "wipe", "erase", "remove", "apply", "run", "exec", "execute", "send", "post",
    "publish", "deploy", "upload", "merge", "invoke", "launch", "kill", "set",
}


def _emit(fmt, allow, reason=""):
    if fmt == "claude":
        if allow:
            return 0
        print(f"BLOCKED by spec_gate_hook: {reason}", file=sys.stderr)
        return 2
    payload = {"decision": "allow"} if allow else {"decision": "deny", "reason": reason}
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _load_rules(rules_path):
    p = pathlib.Path(rules_path)
    if not p.is_file():
        raise ValueError(f"missing gate rules: {p.as_posix()}")
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema") != "dw-spec-gate/1":
        raise ValueError(f"invalid gate rules schema in {p.as_posix()}")
    return data


def _extract_payload(raw_text):
    if not raw_text or not raw_text.strip():
        return None, "empty hook input (fail-closed)"
    try:
        data = json.loads(raw_text)
    except ValueError as exc:
        return None, f"malformed JSON hook input: {exc}"
    if not isinstance(data, dict):
        return None, "hook input must be a JSON object"
    tc = data.get("toolCall") if isinstance(data.get("toolCall"), dict) else None
    if tc is not None:
        tool = tc.get("name")
        args = _COMMON.parse_raw_args(tc)
    else:
        tool = data.get("tool_name")
        ti = data.get("tool_input")
        args = {k: _COMMON.unwrap_arg_value(v) for k, v in ti.items()} if isinstance(ti, dict) else {}
    if not isinstance(tool, str) or not tool.strip():
        return None, "hook input missing tool name"
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and data["cwd"].strip() else None
    ws = data.get("workspacePaths") if isinstance(data.get("workspacePaths"), list) else []
    if cwd is None and ws and isinstance(ws[0], str) and ws[0].strip():
        cwd = ws[0].strip()
    return {"tool": tool.strip(), "args": args, "cwd": cwd, "raw": data}, None


def _rel_match(rel_posix, patterns):
    rel = rel_posix.strip("/")
    for pat in patterns:
        pat_rx = _glob_to_regex(pat, anchored_root=True)
        if pat_rx.fullmatch(rel):
            return True
    return False


def _glob_to_regex(glob_str, anchored_root=False):
    g = glob_str.replace("\\", "/")
    prefix = ""
    if not anchored_root and g.startswith("~/"):
        g = g[2:]
        prefix = re.escape(pathlib.Path.home().resolve().as_posix().rstrip("/")) + "/"
    out, i = prefix, 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            ch = g[i]
            out += {"*": "[^/]*", "?": "[^/]"}.get(ch, re.escape(ch))
            i += 1
    return re.compile(out, re.I if os.name == "nt" else 0)


def _abs_glob_match(abs_path, patterns, hg):
    home = hg.rule_path(str(pathlib.Path.home()), None)
    return hg.globs_match(patterns, [], abs_path, home)


def _resolve_target_path(raw_path, cwd, hg):
    """Return (rule_path_str, pathlib_Path, is_device_or_bad)."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, None, "empty path"
    val = raw_path.strip()
    if not pathlib.Path(os.path.expanduser(val)).is_absolute() and not cwd:
        # Check plain device prefix first
        if os.name == "nt" and hg.DEVICE.match(val) and not hg.plain(val):
            return None, None, "blocked device path"
        return None, None, "relative path without cwd"
    if os.name == "nt":
        m_dev = hg.DEVICE.match(val)
        if m_dev and not hg.plain(val):
            return None, None, "blocked device path"
    rp = hg.rule_path(val, cwd)
    stripped = _COMMON._strip_nt_ext(os.path.expanduser(hg.plain(val) or val))
    p = pathlib.Path(stripped)
    if not p.is_absolute():
        p = pathlib.Path(cwd) / p
    try:
        resolved = pathlib.Path(_COMMON._strip_nt_ext(p.resolve().as_posix()))
    except (OSError, RuntimeError, ValueError):
        resolved = p
    return rp, resolved, None


def _rel_under(child_path, parent_path):
    try:
        c = pathlib.Path(_COMMON._strip_nt_ext(child_path.resolve().as_posix()))
        p = pathlib.Path(_COMMON._strip_nt_ext(parent_path.resolve().as_posix()))
        if os.name == "nt":
            cp = c.as_posix().lower().rstrip("/")
            pp = p.as_posix().lower().rstrip("/")
            if cp == pp:
                return ""
            if cp.startswith(pp + "/"):
                return c.as_posix()[len(p.as_posix().rstrip("/")) + 1:]
            return None
        return c.relative_to(p).as_posix()
    except (OSError, ValueError, RuntimeError):
        return None


def _find_repo_for_target(target_path):
    """Find git checkout enclosing `target_path`, walking up past missing leaf files/dirs."""
    cur = target_path
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    probe = cur if cur.is_dir() else cur.parent
    return _COMMON.find_repo(probe)


def _extract_mcp_info(tool, args):
    """Normalize `call_mcp_tool` or `mcp_*` / `mcp__*` into `(server, action, inner_args)`."""
    if tool == "call_mcp_tool":
        srv = str(args.get("ServerName") or args.get("server") or "").strip().lower()
        act = str(args.get("ToolName") or args.get("tool") or "").strip()
        inner = args.get("Arguments")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except ValueError:
                inner = {}
        return srv, act.lower(), (inner if isinstance(inner, dict) else {})
    if tool.startswith("mcp__"):
        parts = tool[5:].split("__", 1)
        srv = parts[0].lower() if len(parts) == 2 else ""
        act = parts[-1]
        act_snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", act).lower()
        return srv, re.sub(r"[^a-z0-9]+", "_", act_snake).strip("_"), args
    if tool.startswith("mcp_"):
        body = tool[4:].lower()
        # Handle mcp_agy-fast_fast_quick_outline or mcp_files_trash_file
        for marker in ("_fast_", "__"):
            if marker in body:
                srv, act = body.split(marker, 1)
                return srv, act, args
        parts = body.split("_", 1)
        return (parts[0], parts[1], args) if len(parts) == 2 else ("", body, args)
    return None, None, args


def _mcp_is_readonly(act, prefixes):
    if not act:
        return False
    tokens = [t for t in re.split(r"[^a-z0-9]+", act.lower()) if t]
    if not tokens:
        return False
    if act.lower() in prefixes:
        return True
    if any(t in MUTATING_MCP_VERBS for t in tokens):
        return False
    return tokens[0] in prefixes


def _extract_write_targets(tool, args, rules=None):
    """Extract target file paths from built-in write tools or `apply_edits` MCP calls."""
    targets = []
    if tool == "call_mcp_tool" or tool.startswith("mcp"):
        srv, act, inner = _extract_mcp_info(tool, args)
        if act == "apply_edits" or act.endswith("_apply_edits"):
            for item in inner.get("edits") or []:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    targets.append(item["path"])
            return True, targets
        return False, targets
    if rules and tool not in (rules.get("write_tools") or []):
        return False, targets
    for k in ("Path", "TargetFile", "file_path", "path", "notebook_path", "AbsolutePath"):
        if k in args and args[k] is not None:
            targets.append(args[k])
    if not targets:
        return True, [None]
    return True, targets


def _extract_shell_commands(tool, args, rules):
    """Return `(is_shell, [(cmd_str, shell_flavor)])` supporting `run_command` and `run_checks`."""
    if tool in rules["shell_tools"]:
        for k in ("CommandLine", "command", "cmd"):
            if k in args:
                val = args[k]
                flavor = "powershell" if (tool == "PowerShell" or (tool == "run_command" and os.name == "nt")) else "bash"
                return True, [(val if isinstance(val, str) else "", flavor)]
        return True, [("", "bash")]
    if tool == "call_mcp_tool" or tool.startswith("mcp"):
        _, act, inner = _extract_mcp_info(tool, args)
        if act == "run_checks" or act.endswith("_run_checks"):
            cmds = []
            for item in inner.get("commands") or []:
                if isinstance(item, dict) and isinstance(item.get("command"), str):
                    cmds.append((item["command"], "powershell" if os.name == "nt" else "bash"))
            return True, cmds or [("", "bash")]
    return False, []


def _always_blocked_path(target_rp, resolved_p, rules, director_p, hg, rules_path=None):
    canonical_self = {hg.rule_path(str(pathlib.Path(__file__)), None)}
    if rules_path:
        canonical_self.add(hg.rule_path(str(rules_path), None))
    if target_rp in canonical_self:
        return f"protected hook/config path `{resolved_p.as_posix()}` cannot be modified"
    globs = list(rules.get("always_block_globs") or [])
    globs += [
        f"**/{HERE.name}/spec_gate_hook.py",
        f"**/{HERE.name}/GATE_RULES.json",
    ]
    if _abs_glob_match(target_rp, globs, hg):
        return f"protected hook/config path `{resolved_p.as_posix()}` cannot be modified"
    rel_dir = _rel_under(resolved_p, director_p)
    if rel_dir is not None and _rel_match(rel_dir, rules.get("always_block_director_rel") or []):
        return f"protected DIRECTOR path `{resolved_p.as_posix()}` (`{rel_dir}`) cannot be modified by agent tools"
    return None


def _shell_always_blocked(cmd_str, flavor, rules, director_p, cwd, hg):
    """Block self-approval (`spec_gate.py approve`, `gate_check.py --snapshot`) and writes to protected hook/tool files."""
    if not cmd_str or not cmd_str.strip():
        return "empty shell command"
    cmd_clean = hg.strip_heredocs(cmd_str) if flavor == "bash" else cmd_str
    simple = list(hg.simple_commands(hg.FD_DUP.sub(" ", cmd_clean), flavor))
    for name, rest in simple:
        low_args = [a.lower() for a in rest]
        joined = " ".join(low_args)
        if "spec_gate.py" in joined and "approve" in low_args:
            return "spec_gate.py approve must be run by the user/Director CLI, not by tool hooks"
        if "gate_check.py" in joined and "--snapshot" in low_args:
            return "gate_check.py --snapshot cannot be self-invoked by an agent tool"

    targets = hg.redirect_targets(cmd_clean, flavor)
    prot_names = {"spec_gate_hook.py", "gate_rules.json", "seatbelt_hook.py", "hooks.json", "plugins.json"}
    if hg.self_write(simple, prot_names, targets):
        return "shell write to protected hook/gate configuration is forbidden"
    home = hg.rule_path(str(pathlib.Path.home()), None)
    globs = list(rules.get("always_block_globs") or [])
    dir_abs = director_p.resolve().as_posix().rstrip("/")
    for rel in rules.get("always_block_director_rel") or []:
        globs.append(f"{dir_abs}/{rel.lstrip('/')}")
    if hg.shell_write_match({"globs": globs, "except": []}, simple, targets, cwd, home, cmd_clean):
        return "shell write to protected DIRECTOR tools/adapters/brief-history/hook files is forbidden"
    return None


def _iter_prebrief_commands(cmd_str, flavor, hg, depth=0):
    """Yield `(name, rest, problem)` using raw `hg.parse` before env stripping."""
    cmd_clean = hg.strip_heredocs(cmd_str) if flavor == "bash" else cmd_str
    for t in hg.redirect_targets(cmd_clean, flavor):
        if t.lower() not in SAFE_REDIRECT_TARGETS:
            yield None, None, f"shell output redirect `{t or '>'}` blocked before BRIEF.md passes spec_gate"
            return
    for raw_words in hg.parse(hg.FD_DUP.sub(" ", cmd_clean), flavor):
        words = list(raw_words)
        while words:
            if re.fullmatch(r"\w+=.*", words[0]):
                yield None, None, f"inline environment override `{words[0]}` is blocked before BRIEF.md passes spec_gate"
                return
            cname = hg.command_name(words[0])
            if cname == "env":
                idx = 1
                while idx < len(words) and words[idx] in ("-i", "--ignore-environment"):
                    idx += 1
                if idx >= len(words) or words[idx].startswith("-") or re.fullmatch(r"\w+=.*", words[idx]):
                    yield None, None, "`env` with variables/flags other than `-i` is blocked before BRIEF.md passes spec_gate"
                    return
                words = words[idx:]
                continue
            rest = words[1:]
            yield cname, rest, None
            inner = hg.string_run(cname, rest)
            if inner:
                if depth >= 8:
                    yield None, None, "nested shell wrapper depth limit exceeded before BRIEF.md passes spec_gate"
                    return
                yield from _iter_prebrief_commands(inner[0], inner[1], hg, depth + 1)
            break


def _is_git_readonly(rest):
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        tok = rest[i]
        low = tok.lower()
        if (
            low in ("--output", "-o", "-p", "--paginate", "-c", "--config-env", "--exec-path")
            or low.startswith(("--output=", "--config-env=", "--exec-path="))
            or tok.startswith("-c")
        ):
            return False
        if tok in ("-C", "--git-dir", "--work-tree", "--namespace"):
            i += 2
            continue
        i += 1
    if i >= len(rest):
        return False
    sub = rest[i].lower()
    args = rest[i + 1:]
    if any(a.lower() in ("--output", "--ext-diff") or a.lower().startswith("--output=") for a in args):
        return False
    if sub == "grep":
        for a in args:
            low_a = a.lower()
            if a == "-O" or low_a == "--open-files-in-pager" or low_a.startswith("--open-files-in-pager="):
                return False
            if a.startswith("-") and not a.startswith("--") and "O" in a[1:]:
                return False
    if sub in ("status", "log", "diff", "show", "rev-parse", "describe", "cat-file",
               "ls-files", "ls-tree", "check-ignore", "grep", "shortlog", "blame", "name-rev"):
        return True
    if sub == "branch":
        k = 0
        while k < len(args):
            a = args[k]
            if a in GIT_BRANCH_VALUE_FLAGS:
                k += 2
                continue
            if any(a.startswith(f + "=") for f in GIT_BRANCH_VALUE_FLAGS):
                k += 1
                continue
            if a in SAFE_GIT_BRANCH_FLAGS:
                k += 1
                continue
            if a == "--list" or a.startswith("--list="):
                k += 1
                continue
            # Positional glob allowed only immediately after --list
            if k > 0 and args[k - 1] == "--list" and not a.startswith("-"):
                k += 1
                continue
            return False
        return True
    if sub == "tag":
        return not args or all(a in ("-l", "--list", "-n") for a in args)
    if sub == "remote":
        return not args or args in (["-v"], ["show"]) or (len(args) == 2 and args[0] == "show")
    if sub == "worktree":
        return args[:1] == ["list"]
    if sub == "stash":
        return args[:1] in (["list"], ["show"])
    return False


def _resolve_script_path(token, cwd):
    p = pathlib.Path(os.path.expanduser(token))
    if not p.is_absolute():
        p = pathlib.Path(cwd) / p if cwd else pathlib.Path.cwd() / p
    try:
        return pathlib.Path(_COMMON._strip_nt_ext(p.resolve().as_posix()))
    except (OSError, RuntimeError, ValueError):
        return p


def _is_readonly_python_call(rest, cwd, director_p, tools_p, rules):
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        tok = rest[i]
        if tok in ("-c", "-m") or tok.startswith("-c") or tok.startswith("-m"):
            return False
        if tok == "-X":
            i += 2
            continue
        i += 1
    if i >= len(rest):
        return False
    script_tok = rest[i]
    script_p = _resolve_script_path(script_tok, cwd)
    script_args = rest[i + 1:]
    allowed_roots = [
        tools_p.resolve(),
        (director_p / "adapters" / "antigravity").resolve(),
        HERE.resolve(),
        _COMMON.find_base_tools(HERE).resolve(),
    ]
    in_allowed_root = any(_rel_under(script_p, root) is not None for root in allowed_roots)
    if not in_allowed_root or not script_p.is_file():
        return False
    allowed_map = rules.get("readonly_dw_scripts") or {}
    if script_p.name not in allowed_map:
        return False
    if "--snapshot" in [a.lower() for a in script_args]:
        return False
    req_subs = allowed_map[script_p.name]
    if not req_subs:
        return True
    value_flags = {
        "--brief", "--snapshot-dir", "--director", "--repo", "--log",
        "--transcript", "--conversation", "--brain-root", "--out", "--config",
        "--audit", "--tools", "--rules", "--from", "--to", "--since",
        "--until", "--cite", "--key", "--answers",
    }
    pos, j = [], 0
    while j < len(script_args):
        tok = script_args[j]
        low_t = tok.lower()
        if low_t in value_flags:
            j += 2
            continue
        if low_t.startswith("-"):
            j += 1
            continue
        pos.append(low_t)
        j += 1
    if any(x in ("approve", "hook-append", "build") for x in pos):
        return False
    return bool(pos) and pos[0] in req_subs


PS_SCRIPTBLOCK_CMDS = {
    "where", "where-object", "select", "select-object",
    "sort", "sort-object", "format-table", "ft", "format-list", "fl",
    "measure-object", "get-childitem", "gci",
}


def _readonly_cmd_flag_problem(name, rest):
    """Reject file-writing or command-executing flags on allowlisted read-only shell commands."""
    if name in PS_SCRIPTBLOCK_CMDS and any("{" in tok or "}" in tok for tok in rest):
        return f"`{name}` with PowerShell scriptblock `{{...}}` is blocked before BRIEF.md passes spec_gate"
    if name == "sort":
        for tok in rest:
            low = tok.lower()
            if (
                low in ("-o", "--output", "/o", "/output", "--compress-program")
                or low.startswith(("--output=", "--compress-program=", "/o:", "/output:"))
                or (tok.startswith("-") and not tok.startswith("--") and "o" in tok[1:])
            ):
                return f"sort flag `{tok}` blocked before BRIEF.md passes spec_gate"
    elif name == "tree":
        for tok in rest:
            low = tok.lower()
            if (
                low in ("-o", "--output")
                or low.startswith("--output=")
                or (tok.startswith("-") and not tok.startswith("--") and "o" in tok[1:].lower())
            ):
                return f"tree flag `{tok}` blocked before BRIEF.md passes spec_gate"
    elif name in ("less", "more"):
        for tok in rest:
            low = tok.lower()
            if (
                low in ("-o", "--log-file")
                or low.startswith("--log-file=")
                or tok.startswith("+!")
                or (tok.startswith("-") and not tok.startswith("--") and ("o" in tok[1:] or "O" in tok[1:]))
            ):
                return f"{name} flag `{tok}` blocked before BRIEF.md passes spec_gate"
    elif name == "bat":
        for tok in rest:
            low = tok.lower()
            if low == "--pager" or low.startswith("--pager="):
                return f"bat flag `{tok}` blocked before BRIEF.md passes spec_gate"
    elif name == "file":
        for tok in rest:
            low = tok.lower()
            if low == "--compile" or low.startswith("--compile=") or (tok.startswith("-") and not tok.startswith("--") and "C" in tok[1:]):
                return f"file flag `{tok}` blocked before BRIEF.md passes spec_gate"
    elif name == "printf":
        for tok in rest:
            if tok == "--":
                break
            if tok.startswith("-") and not tok.startswith("--") and "v" in tok[1:]:
                return f"printf flag `{tok}` blocked before BRIEF.md passes spec_gate"
    elif name == "command":
        has_lookup = any(tok in ("-v", "-V") for tok in rest)
        bad_opts = [tok for tok in rest if tok.startswith("-") and tok not in ("-v", "-V")]
        if not has_lookup or bad_opts:
            return "`command` without pure `-v`/`-V` lookup is blocked before BRIEF.md passes spec_gate"
    return None


def _prebrief_shell_problem(cmd_str, flavor, rules, director_p, tools_p, cwd, hg):
    if not cmd_str or not cmd_str.strip():
        return "empty shell command before BRIEF.md passes spec_gate"
    saw_any = False
    for name, rest, env_prob in _iter_prebrief_commands(cmd_str, flavor, hg):
        if env_prob:
            return env_prob
        saw_any = True
        if name in ("bash", "sh", "zsh", "dash", "ksh", "cmd", "powershell", "pwsh", "eval", "invoke-expression", "iex"):
            if hg.string_run(name, rest) is None:
                return f"shell script/interactive `{name}` blocked before BRIEF.md passes spec_gate"
            continue
        if name == "git":
            if not _is_git_readonly(rest):
                return "non-read-only git command blocked before BRIEF.md passes spec_gate"
            continue
        if name == "find":
            for a in rest:
                low_a = a.lower()
                if low_a.startswith("-") and low_a not in SAFE_FIND_FLAGS:
                    return f"find flag `{a}` blocked before BRIEF.md passes spec_gate"
            continue
        if name == "rg" and any(a == "--pre" or a.startswith("--pre=") for a in rest):
            return "rg --pre blocked before BRIEF.md passes spec_gate"
        if name in READONLY_PYTHON_BASES:
            if not _is_readonly_python_call(rest, cwd, director_p, tools_p, rules):
                return "Python execution outside read-only DIRECTOR/tools checks blocked before BRIEF.md passes spec_gate"
            continue
        if name in READONLY_SHELL_CMDS:
            flag_prob = _readonly_cmd_flag_problem(name, rest)
            if flag_prob:
                return flag_prob
            continue
        return f"command `{name}` blocked before BRIEF.md passes spec_gate"
    if not saw_any:
        return "unrecognized empty shell command"
    return None


def _prebrief_write_problem(raw_path, target_rp, resolved_p, err, rules, brief_p, director_p):
    if err:
        return f"write blocked before BRIEF.md passes spec_gate ({err})"
    if os.path.normcase(resolved_p.as_posix()) == os.path.normcase(brief_p.resolve().as_posix()) if os.name == "nt" else (resolved_p.resolve() == brief_p.resolve()):
        return None
    rel_dir = _rel_under(resolved_p, director_p)
    if rel_dir is not None:
        if _rel_match(rel_dir, rules.get("director_config_rel") or []):
            return f"DIRECTOR config `{rel_dir}` cannot be edited before BRIEF.md passes spec_gate"
        if _rel_match(rel_dir, rules.get("director_allow") or []):
            return None
        return f"DIRECTOR path `{rel_dir}` is not in pre-brief allowlist before BRIEF.md passes spec_gate"
    repo = _find_repo_for_target(resolved_p)
    if repo is None:
        return None  # artifact / temp / outside git repos
    rel_repo = _rel_under(resolved_p, repo.root)
    if rel_repo is not None and _rel_match(rel_repo, rules.get("repo_root_allow") or []):
        return None
    return f"repo write `{resolved_p.as_posix()}` blocked until BRIEF.md passes spec_gate.py"


def evaluate_call(parsed, brief_p, director_p, tools_p, rules, hg, sg, rules_path=None):
    tool = parsed["tool"]
    args = parsed["args"]
    cwd = parsed["cwd"]

    # 1. Always-block checks (never bypassed even when BRIEF.md is PASS)
    is_write, raw_targets = _extract_write_targets(tool, args, rules)
    resolved_targets = []
    if is_write:
        for rt in raw_targets:
            rp, res_p, err = _resolve_target_path(rt, cwd, hg)
            resolved_targets.append((rt, rp, res_p, err))
            if err is None:
                blk = _always_blocked_path(rp, res_p, rules, director_p, hg, rules_path=rules_path)
                if blk:
                    return False, f"[R6] {blk}"

    is_shell, shell_cmds = _extract_shell_commands(tool, args, rules)
    if is_shell:
        for cmd_str, flavor in shell_cmds:
            blk = _shell_always_blocked(cmd_str, flavor, rules, director_p, cwd, hg)
            if blk:
                return False, f"[R6] {blk}"

    # 2. Fast allow for pure read-only UI/search/file tools and read-only research subagents
    if tool in rules.get("read_tools", []):
        return True, ""

    if tool in rules.get("dispatch_tools", []):
        sub_t = str(args.get("TypeName") or args.get("subagent_type") or "").strip()
        iso = str(args.get("Workspace") or args.get("isolation") or "inherit").strip().lower()
        if sub_t in rules.get("read_subagents", []) and iso not in ("branch", "share", "worktree"):
            return True, ""

    if tool == "call_mcp_tool" or tool.startswith("mcp"):
        srv, act, _ = _extract_mcp_info(tool, args)
        if not is_write and not is_shell and _mcp_is_readonly(act, rules.get("mcp_read_prefixes", [])):
            return True, ""

    # 3. Live BRIEF.md gate (never cached)
    try:
        folder = sg.default_folder(brief_p)
        status, lines = sg.check(brief_p, folder, approve=False)
    except Exception as exc:
        return False, f"[R6] spec_gate check error ({type(exc).__name__}: {exc}); fix DIRECTOR/BRIEF.md first"

    if status == "PASS":
        return True, ""

    why_hint = "; ".join(l.strip() for l in (lines or [])[:2]) or f"spec_gate status={status}"
    prefix = f"[R6] BRIEF.md 未通過 spec_gate（{status}：{why_hint}）— "

    # 4. Pre-brief strict checks
    if is_write:
        for rt, rp, res_p, err in resolved_targets:
            prob = _prebrief_write_problem(rt, rp, res_p, err, rules, brief_p, director_p)
            if prob:
                return False, prefix + prob
        return True, ""

    if is_shell:
        for cmd_str, flavor in shell_cmds:
            prob = _prebrief_shell_problem(cmd_str, flavor, rules, director_p, tools_p, cwd, hg)
            if prob:
                return False, prefix + prob
        return True, ""

    if tool in rules.get("dispatch_tools", []):
        return False, prefix + "cannot spawn coding/isolated subagent before BRIEF.md passes spec_gate"

    return False, prefix + f"tool `{tool}` blocked before BRIEF.md passes spec_gate"


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brief", default="DIRECTOR/BRIEF.md", help="path to DIRECTOR/BRIEF.md")
    ap.add_argument("--director", help="DIRECTOR folder path (default: parent of --brief)")
    ap.add_argument("--tools", help="path to installed base DIRECTOR/tools directory")
    ap.add_argument("--rules", default=str(HERE / "GATE_RULES.json"), help="path to GATE_RULES.json")
    ap.add_argument("--format", choices=("antigravity", "claude"), default="antigravity")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    try:
        rules = _load_rules(a.rules)
    except Exception as exc:
        return _emit(a.format, False, f"[R6] unreadable GATE_RULES.json ({exc}); fail-closed")

    raw_stdin = sys.stdin.read() if sys.stdin is not None else ""
    parsed, p_err = _extract_payload(raw_stdin)
    if p_err:
        return _emit(a.format, False, f"[R6] {p_err}")

    try:
        tools_p = pathlib.Path(a.tools).resolve() if a.tools else _COMMON.find_base_tools(HERE).resolve()
        hg = _COMMON._load_module(tools_p / "hook_guard.py", "_agy_sg_hg_")
        sg = _COMMON._load_module(tools_p / "spec_gate.py", "_agy_sg_sg_")
    except Exception as exc:
        return _emit(a.format, False, f"[R6] cannot load spec_gate/hook_guard ({exc}); fail-closed")

    cwd_base = pathlib.Path(parsed["cwd"]).resolve() if parsed["cwd"] else pathlib.Path.cwd()
    brief_raw = pathlib.Path(a.brief)
    brief_p = brief_raw if brief_raw.is_absolute() else (cwd_base / brief_raw)
    director_p = pathlib.Path(a.director) if a.director else brief_p.parent
    if not director_p.is_absolute():
        director_p = cwd_base / director_p

    try:
        rules_p = pathlib.Path(a.rules).resolve()
        allow, reason = evaluate_call(
            parsed, brief_p.resolve(), director_p.resolve(), tools_p, rules, hg, sg, rules_path=rules_p
        )
    except Exception as exc:
        return _emit(a.format, False, f"[R6] unexpected spec_gate_hook error ({type(exc).__name__}: {exc}); fail-closed")
    return _emit(a.format, allow, reason)


if __name__ == "__main__":
    sys.exit(main())
