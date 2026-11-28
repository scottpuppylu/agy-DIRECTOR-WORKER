#!/usr/bin/env python3
"""Shared stdlib helpers for the Antigravity adapter (3.0.0-agy.7).

Delegates atomic writes, git folder/worktree/common-dir discovery, JSONL tail
reading, G2 exit-code parsing, and D7 usage number validation to the frozen
base `tools/_common.py` module without duplicating upstream implementations.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent


def find_base_tools(here=None):
    """Locate the base DIRECTOR-WORK `tools/` directory containing `_common.py`."""
    base = pathlib.Path(here or HERE).resolve()
    candidates = (
        base.parent.parent / "tools",
        base / "tools",
        base.parent / "tools",
        base,
    )
    for cand in candidates:
        if (cand / "_common.py").is_file():
            return cand
    raise FileNotFoundError(f"cannot locate base tools/_common.py relative to {base.as_posix()}")


def _load_module(path, cache_prefix):
    resolved = pathlib.Path(path).resolve()
    digest = hashlib.sha256(os.path.normcase(resolved.as_posix()).encode("utf-8")).hexdigest()[:10]
    mod_name = f"{cache_prefix}{resolved.stem}_{digest}"
    cached = sys.modules.get(mod_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(mod_name, resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create module spec for {resolved.as_posix()}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_base_tool(name, here=None, prefix="_dw_base_"):
    """Load and cache a frozen base tool module `<tools_dir>/<name>.py`."""
    tools_dir = find_base_tools(here)
    target = tools_dir / f"{name}.py"
    if not target.is_file():
        raise FileNotFoundError(f"base tool missing: {target.as_posix()}")
    return _load_module(target, prefix)


def load_sibling(here, name, prefix="_agy_"):
    """Load a sibling module in `here/<name>.py`, falling back to base `tools/<name>.py`."""
    local = pathlib.Path(here).resolve() / f"{name}.py"
    if local.is_file():
        return _load_module(local, prefix)
    return load_base_tool(name, here=here, prefix=prefix)


_BASE_COMMON = load_base_tool("_common", here=HERE)

write_bytes_atomic = _BASE_COMMON.write_bytes_atomic
write_text_atomic = _BASE_COMMON.write_text_atomic
write_json_atomic = _BASE_COMMON.write_json_atomic
git_dir = _BASE_COMMON.git_dir
git_common_dir = _BASE_COMMON.git_common_dir
find_repo = _BASE_COMMON.find_repo
same_repo = _BASE_COMMON.same_repo
exclude_file = _BASE_COMMON.exclude_file
read_tail = _BASE_COMMON.read_tail
tail_lines = _BASE_COMMON.tail_lines
tail_jsonl = _BASE_COMMON.tail_jsonl
EXIT_LINE = _BASE_COMMON.EXIT_LINE
exit_code_from = _BASE_COMMON.exit_code_from
gate_status = _BASE_COMMON.gate_status
usage_number = _BASE_COMMON.usage_number
usage_numbers = _BASE_COMMON.usage_numbers
usage_partial = _BASE_COMMON.usage_partial
partial_line = _BASE_COMMON.partial_line


def _strip_nt_ext(text):
    """Drop Windows \\\\?\\ drive/UNC extended prefix while preserving valid UNC shares."""
    if os.name != "nt" or not isinstance(text, str):
        return text
    s = text.replace("\\", "/")
    if s.startswith("//?/UNC/"):
        return "//" + s[8:]
    if s.startswith("//?/"):
        return s[4:]
    return text


def norm_path_key(path_like):
    """Canonical path comparison key (forward slashes, case-folded only on Windows)."""
    raw = str(path_like or "").strip()
    if not raw:
        return ""
    try:
        resolved = _strip_nt_ext(pathlib.Path(raw).resolve().as_posix())
    except (OSError, RuntimeError, ValueError):
        resolved = _strip_nt_ext(raw).replace("\\", "/")
    return os.path.normcase(resolved) if os.name == "nt" else resolved


def case_norm(text):
    """Case-fold string on Windows while preserving exact case on POSIX filesystems."""
    return text.lower() if os.name == "nt" else text


def find_git_root_and_common(start_path):
    """Return `(root, common_dir, is_linked_worktree)` using frozen `tools/_common.find_repo`."""
    repo = find_repo(start_path)
    if repo is None:
        return None, None, False
    return repo.root, repo.common_dir, bool(repo.linked)


def unwrap_arg_value(val):
    """Unwrap Antigravity PLANNER_RESPONSE JSON-quoted string args (e.g. '\"git status\"' -> 'git status')."""
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
            try:
                decoded = json.loads(s)
                if isinstance(decoded, (str, int, float, bool, list, dict)) or decoded is None:
                    return decoded
            except ValueError:
                pass
    return val


def parse_raw_args(tc):
    """Extract tool call args dict supporting Antigravity `args` / `arguments` (dict or JSON string)."""
    if not isinstance(tc, dict):
        return {}
    for key in ("args", "arguments", "tool_input", "input"):
        raw = tc.get(key)
        if isinstance(raw, dict):
            return {k: unwrap_arg_value(v) for k, v in raw.items()}
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return {k: unwrap_arg_value(v) for k, v in parsed.items()}
            except ValueError:
                pass
    return {}


def scan_tail_for_step(path, step_idx, require_generic=False, max_scan_bytes=2 * 1024 * 1024):
    """Scan JSONL backwards in bounded chunks for an entry with exact `step_index == step_idx`."""
    if not path or step_idx is None:
        return None
    tp = pathlib.Path(path)
    if not tp.is_file():
        return None
    try:
        idx_int = int(step_idx)
    except (TypeError, ValueError):
        return None
    idx_bytes_a = f'"step_index":{idx_int}'.encode("ascii")
    idx_bytes_b = f'"step_index": {idx_int}'.encode("ascii")
    try:
        size = tp.stat().st_size
    except OSError:
        return None
    start = max(0, size - max_scan_bytes)
    try:
        with open(tp, "rb") as fh:
            fh.seek(start)
            data = fh.read(size - start)
    except OSError:
        return None
    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]
    for raw_line in reversed(lines):
        if not raw_line.strip():
            continue
        if idx_bytes_a not in raw_line and idx_bytes_b not in raw_line:
            continue
        if require_generic and b'"GENERIC"' not in raw_line:
            continue
        try:
            obj = json.loads(raw_line.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("step_index") != idx_int:
            continue
        if require_generic and obj.get("type") != "GENERIC":
            continue
        return obj
    return None


def split_cmd_tokens(cmd_str):
    """Split a shell command line into unquoted tokens cleanly across quotes."""
    if not cmd_str or not isinstance(cmd_str, str):
        return []
    toks, cur, quote = [], [], None
    for ch in cmd_str:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in ('"', "'"):
            quote = ch
        elif ch.isspace():
            if cur:
                toks.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        toks.append("".join(cur))
    return toks


def extract_opt(tokens, flag):
    """Return the first value passed to `--flag VAL` or `--flag=VAL`."""
    for i, t in enumerate(tokens):
        if t == flag and i + 1 < len(tokens):
            return tokens[i + 1]
        if t.startswith(flag + "="):
            return t.split("=", 1)[1]
    return None


def extract_all_opts(tokens, flag):
    """Return all values passed to repeatable `--flag VAL` / `--flag=VAL`."""
    vals = []
    for i, t in enumerate(tokens):
        if t == flag and i + 1 < len(tokens):
            vals.append(tokens[i + 1])
        elif t.startswith(flag + "="):
            vals.append(t.split("=", 1)[1])
    return vals


def lf_sha256(path):
    """Return hex SHA-256 of `path` with CRLF normalized to LF."""
    data = pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def main(argv=None):
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    print("agy_common: shared helpers for adapters/antigravity/ (delegating to tools/_common.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
