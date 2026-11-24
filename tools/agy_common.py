#!/usr/bin/env python3
"""Shared stdlib-only helpers for agy-DIRECTOR-WORKER tools (agydw 2.6.0-agy.6).

Centralizes module loading/caching, JSON argument unwrapping, path normalization,
atomic writes, pure-FS git root/common-dir discovery, bounded tail JSONL scanning,
and CLI token parsing so tools stay consistent without external dependencies.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

_MODULE_CACHE = {}


def load_sibling(base_dir, name, prefix="_agy_mod_"):
    """Load and cache a sibling Python module from base_dir without mutating sys.path."""
    base = pathlib.Path(base_dir).resolve()
    key = (str(base), str(name))
    cached = _MODULE_CACHE.get(key)
    if cached is not None:
        return cached
    mod_path = base / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{prefix}{name}", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MODULE_CACHE[key] = mod
    return mod


def case_norm(s):
    """Preserve exact casing on POSIX filesystems while staying case-insensitive on Windows."""
    return s.lower() if os.name == "nt" else s


def norm_path_key(path_str):
    """Return canonical normcase POSIX path key for comparison without raising on invalid paths."""
    try:
        return os.path.normcase(pathlib.Path(str(path_str)).resolve().as_posix())
    except (OSError, ValueError, RuntimeError):
        return os.path.normcase(str(path_str).replace("\\", "/"))


def unwrap_arg_value(val):
    """Unwrap Antigravity PLANNER_RESPONSE JSON-quoted string args (e.g. '\"git status\"' -> 'git status')."""
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or s[0] in "[{"):
            try:
                return json.loads(s)
            except ValueError:
                return val
    return val


def parse_raw_args(tc):
    """Extract tool call args dict supporting both Antigravity `args` and `arguments` (dict or JSON string)."""
    if not isinstance(tc, dict):
        return {}
    raw = tc.get("args")
    if raw is None:
        raw = tc.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def write_text_atomic(path, text):
    """Write UTF-8 LF text atomically via same-directory temporary file + os.replace."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=str(p.parent), prefix=f".tmp_{p.name}_", delete=False
    ) as fh:
        tmp_path = pathlib.Path(fh.name)
        fh.write(text)
    try:
        os.replace(tmp_path, p)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write_json_atomic(path, obj):
    """Write formatted UTF-8 LF JSON atomically."""
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def git_dir(repo):
    """Resolve <repo>/.git directory using pure filesystem checks (handles linked worktrees)."""
    dot = pathlib.Path(repo) / ".git"
    if dot.is_dir():
        return dot.resolve()
    if dot.is_file():
        try:
            m = re.match(r"gitdir:\s*(.+)", dot.read_text(encoding="utf-8", errors="replace").strip())
            if m:
                p = pathlib.Path(m.group(1).strip())
                return p.resolve() if p.is_absolute() else (pathlib.Path(repo) / p).resolve()
        except OSError:
            return None
    return None


def git_common_dir(repo):
    """Resolve the shared Git common directory for main checkouts and linked worktrees."""
    gd = git_dir(repo)
    if gd is not None:
        commondir_file = gd / "commondir"
        if commondir_file.is_file():
            try:
                rel = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
                if rel:
                    p = pathlib.Path(rel)
                    return p.resolve() if p.is_absolute() else (gd / p).resolve()
            except OSError:
                pass
        return gd
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            p = pathlib.Path(proc.stdout.strip())
            return p.resolve() if p.is_absolute() else (pathlib.Path(repo) / p).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def find_git_root_and_common(start_path):
    """Walk ancestors for .git dir or linked-worktree .git file without spawning git."""
    try:
        cur = pathlib.Path(start_path).resolve()
    except (OSError, ValueError):
        return None, None, False
    if cur.is_file():
        cur = cur.parent
    for cand in (cur, *cur.parents):
        dot = cand / ".git"
        if dot.is_dir():
            return cand, dot.resolve(), False
        if dot.is_file():
            try:
                txt = dot.read_text(encoding="utf-8", errors="replace").strip()
                m = re.match(r"gitdir:\s*(.+)", txt)
                if m:
                    gp = pathlib.Path(m.group(1).strip())
                    gd = gp.resolve() if gp.is_absolute() else (cand / gp).resolve()
                    cd_file = gd / "commondir"
                    if cd_file.is_file():
                        rel = cd_file.read_text(encoding="utf-8", errors="replace").strip()
                        cp = pathlib.Path(rel)
                        cd = cp.resolve() if cp.is_absolute() else (gd / cp).resolve()
                        return cand, cd, True
                    return cand, gd, True
            except OSError:
                return cand, None, True
    return None, None, False


def scan_tail_for_step(path, step_idx, require_generic=False, max_scan_bytes=2 * 1024 * 1024):
    """Scan JSONL backwards in bounded windows using an exact byte regex prefilter before JSON parsing."""
    p = pathlib.Path(path)
    if not p.is_file() or step_idx is None:
        return None
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    try:
        idx_bytes = re.escape(str(int(step_idx)).encode("ascii"))
    except (TypeError, ValueError):
        idx_bytes = re.escape(str(step_idx).encode("ascii", errors="ignore"))
    step_re = re.compile(rb'"step_index"\s*:\s*' + idx_bytes + rb"(?!\d)")
    windows = (65536, 196608, 786432, 1048576)
    pos = size
    scanned = 0
    carry = b""
    w_idx = 0
    try:
        with open(p, "rb") as fh:
            while pos > 0 and scanned < max_scan_bytes:
                win = windows[w_idx] if w_idx < len(windows) else windows[-1]
                w_idx += 1
                win = min(win, pos, max_scan_bytes - scanned)
                if win <= 0:
                    break
                start = pos - win
                fh.seek(start)
                buf = fh.read(win) + carry
                pos = start
                scanned += win
                if start > 0:
                    nl = buf.find(b"\n")
                    if nl >= 0:
                        carry = buf[:nl + 1]
                        complete = buf[nl + 1:]
                    else:
                        carry = buf
                        continue
                else:
                    carry = b""
                    complete = buf
                for line in reversed(complete.splitlines()):
                    if not line.strip() or not step_re.search(line):
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and obj.get("step_index") == step_idx:
                        if require_generic and obj.get("type") != "GENERIC":
                            continue
                        return obj
    except OSError:
        return None
    return None


def split_cmd_tokens(cmd_str):
    """Tokenize a shell command line preserving quoted substrings without backslash-stripping Windows paths."""
    if not cmd_str:
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
    """Return the first value passed to --flag VAL or --flag=VAL."""
    for i, t in enumerate(tokens):
        if t == flag and i + 1 < len(tokens):
            return tokens[i + 1]
        if t.startswith(flag + "="):
            return t.split("=", 1)[1]
    return None


def extract_all_opts(tokens, flag):
    """Return all values passed to --flag VAL or --flag=VAL."""
    out = []
    for i, t in enumerate(tokens):
        if t == flag and i + 1 < len(tokens):
            out.append(tokens[i + 1])
        elif t.startswith(flag + "="):
            out.append(t.split("=", 1)[1])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)
    print("agy_common: stdlib shared helpers module (import from tools/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
