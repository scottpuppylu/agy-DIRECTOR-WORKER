#!/usr/bin/env python3
"""Workspace bootstrap & upgrade helper for agy-DIRECTOR-WORKER (agydw). Dry-run by default; --apply writes.

Idempotently wires a project repository for Antigravity Director/Worker governance WITHOUT
modifying user-global ~/.gemini/config/config.json:
  1. Adds local-only paths (`AGENTS.local.md`, `.agents/hooks.local.json`, `_work/`) into the
     Git common-dir exclude (`git rev-parse --git-path info/exclude` — works for linked worktrees too).
  2. Scaffolds/upgrades a per-project `<WS>/DIRECTOR-<repo>/` folder (bound to the repo's Git
     common-dir fingerprint so sibling repos never collide, while linked worktrees share the
     project's DIRECTOR), plus `<DIRECTOR>/.sessions.json` (`default: "worker"`) and hashed/
     versioned `tools/` with rollback backups on upgrades.
  3. Non-destructively merges <REPO>/.agents/hooks.json (PreToolUse -> agy_guard.py with --protect
     and --sessions, PostToolUse -> agy_audit.py + agy_hook_bridge.py, SessionStart, PreInvocation,
     Stop) and <REPO>/.agents/plugins.json (replacing any older director-work plugin path in-place).
  4. Validates all inputs in an atomic pre-flight before writing a single file.

Usage:
  python -B -I -X utf8 agy_setup.py --repo <REPO> [--director <DIR>] [--init-director] [--apply]
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_EXCLUDES = ("AGENTS.local.md", ".agents/hooks.local.json", "_work/")
MANIFEST_NAME = "AGYDW_TOOLS_MANIFEST.json"


def read_version(rules_root):
    """Read VERSION file from rules_root, falling back safely."""
    vf = pathlib.Path(rules_root) / "VERSION"
    if vf.is_file():
        txt = vf.read_text(encoding="utf-8-sig", errors="replace").strip()
        if txt:
            return txt
    return "2.6.0-agy.3"


def _norm_path_key(path_str):
    try:
        return os.path.normcase(pathlib.Path(path_str).resolve().as_posix())
    except (OSError, ValueError):
        return os.path.normcase(str(path_str).replace("\\", "/"))


def git_dir(repo):
    """Resolve <repo>/.git directory (handles linked git worktrees)."""
    dot = pathlib.Path(repo) / ".git"
    if dot.is_dir():
        return dot.resolve()
    if dot.is_file():
        m = re.match(r"gitdir:\s*(.+)", dot.read_text(encoding="utf-8", errors="replace").strip())
        if m:
            p = pathlib.Path(m.group(1).strip())
            return p.resolve() if p.is_absolute() else (pathlib.Path(repo) / p).resolve()
    return None


def git_common_dir(repo):
    """Resolve the shared Git common directory for main checkouts and linked worktrees."""
    gd = git_dir(repo)
    if gd is not None:
        commondir_file = gd / "commondir"
        if commondir_file.is_file():
            rel = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
            if rel:
                p = pathlib.Path(rel)
                return p.resolve() if p.is_absolute() else (gd / p).resolve()
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


def git_exclude_file(repo):
    """Resolve info/exclude under the Git common directory."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            p = pathlib.Path(proc.stdout.strip())
            return p.resolve() if p.is_absolute() else (pathlib.Path(repo) / p).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    cd = git_common_dir(repo)
    return (cd / "info" / "exclude") if cd is not None else None


def primary_repo_root(repo):
    """Return the primary repo folder owning repo (same across linked worktrees)."""
    rp = pathlib.Path(repo).resolve()
    cd = git_common_dir(rp)
    if cd is not None and cd.name == ".git":
        return cd.parent.resolve()
    return rp


def q(path_like):
    """Format an absolute forward-slash path safely for Windows cmd /c or POSIX sh -c."""
    s = pathlib.Path(path_like).resolve().as_posix()
    if re.fullmatch(r"[A-Za-z0-9_./:{}-]+", s):
        return s
    return f'"{s}"' if os.name == "nt" else shlex.quote(s)


def _is_valid_rules_root(root):
    p = pathlib.Path(root)
    return (p / "templates" / "RESUME_STATE.md").is_file() and (p / "plugin" / "director-work" / "plugin.json").is_file()


def resolve_rules_root(rules_arg, explicit_rules):
    """Locate valid agydw rules root cleanly, using copied manifest/DIRECTOR_STATE hints if needed."""
    if explicit_rules:
        cand = pathlib.Path(rules_arg).resolve()
        if _is_valid_rules_root(cand):
            return cand, None
        return None, f"--rules {cand.as_posix()} is missing templates/RESUME_STATE.md or plugin/director-work/plugin.json"

    candidates = [pathlib.Path(rules_arg).resolve(), HERE.parent]
    manifest = HERE / MANIFEST_NAME
    if manifest.is_file():
        try:
            mdata = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if isinstance(mdata, dict) and isinstance(mdata.get("rules_root"), str):
                candidates.append(pathlib.Path(mdata["rules_root"]))
        except (OSError, ValueError):
            pass
    parent_state = HERE.parent / "DIRECTOR_STATE.md"
    if parent_state.is_file():
        m = re.search(r"^[ \t]*RULES_PATH[ \t]*:[ \t]*(.+)$", parent_state.read_text(encoding="utf-8-sig", errors="replace"), re.M)
        if m and m.group(1).strip():
            candidates.append(pathlib.Path(m.group(1).strip()))

    seen = set()
    for c in candidates:
        key = _norm_path_key(c)
        if key in seen:
            continue
        seen.add(key)
        if _is_valid_rules_root(c):
            return c.resolve(), None
    return None, (
        f"cannot locate agy-DIRECTOR-WORKER templates/ and plugin/ from {HERE.as_posix()}; "
        "pass --rules <AGYDW_REPO>"
    )


def director_owner_info(director_dir):
    """Read project ownership metadata from director_work.json and DIRECTOR_STATE.md."""
    d = pathlib.Path(director_dir)
    info = {"project_repo": None, "project_git_common_dir": None, "rules_path": None}
    cfg_p = d / "director_work.json"
    if cfg_p.is_file():
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8-sig"))
            if isinstance(cfg, dict):
                if isinstance(cfg.get("project_git_common_dir"), str) and cfg["project_git_common_dir"].strip():
                    info["project_git_common_dir"] = cfg["project_git_common_dir"].strip()
                if isinstance(cfg.get("director_worktree"), str) and cfg["director_worktree"].strip():
                    info["project_repo"] = cfg["director_worktree"].strip()
        except (OSError, ValueError):
            pass
    st_p = d / "DIRECTOR_STATE.md"
    if st_p.is_file():
        st = st_p.read_text(encoding="utf-8-sig", errors="replace")
        m_repo = re.search(r"^[ \t]*PROJECT_REPO[ \t]*:[ \t]*(.+)$", st, re.M)
        if m_repo and m_repo.group(1).strip() and not info["project_repo"]:
            info["project_repo"] = m_repo.group(1).strip()
        m_rules = re.search(r"^[ \t]*RULES_PATH[ \t]*:[ \t]*(.+)$", st, re.M)
        if m_rules and m_rules.group(1).strip():
            info["rules_path"] = m_rules.group(1).strip()
    return info


def director_matches_repo(director_dir, repo):
    """Check whether director_dir belongs to repo (or another worktree of the same git common dir)."""
    info = director_owner_info(director_dir)
    owner_cd = info["project_git_common_dir"]
    owner_repo = info["project_repo"]
    if not owner_cd and not owner_repo:
        return True, None

    rp = pathlib.Path(repo).resolve()
    repo_key = _norm_path_key(rp)
    primary_key = _norm_path_key(primary_repo_root(rp))
    repo_cd = git_common_dir(rp)
    repo_cd_key = _norm_path_key(repo_cd) if repo_cd is not None else None

    if owner_cd and repo_cd_key:
        if _norm_path_key(owner_cd) == repo_cd_key:
            return True, None
        return False, owner_repo or owner_cd

    if owner_repo:
        if _norm_path_key(owner_repo) in (repo_key, primary_key):
            return True, None
        owner_p = pathlib.Path(owner_repo)
        if owner_p.is_dir() and repo_cd_key:
            owner_live_cd = git_common_dir(owner_p)
            if owner_live_cd is not None and _norm_path_key(owner_live_cd) == repo_cd_key:
                return True, None
        return False, owner_repo

    return True, None


def default_director_dir(repo):
    """Choose safe per-project DIRECTOR-<repo> path while smoothly upgrading matching legacy DIRECTOR/."""
    primary = primary_repo_root(repo)
    legacy = primary.parent / "DIRECTOR"
    if legacy.is_dir() and ((legacy / "DIRECTOR_STATE.md").is_file() or (legacy / "director_work.json").is_file()):
        ok, _ = director_matches_repo(legacy, repo)
        if ok:
            return legacy
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", primary.name).strip("-") or "project"
    return primary.parent / f"DIRECTOR-{slug}"


def write_text_atomic(path, text):
    """Write UTF-8 LF text via staging temp file + os.replace."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def write_json_atomic(path, obj):
    """Write JSON atomically with stable LF formatting."""
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def compute_tools_hashes(tools_src):
    """Return {rel_name: sha256} for managed files inside rules_root/tools."""
    hashes = {}
    for p in sorted(pathlib.Path(tools_src).iterdir()):
        if not p.is_file():
            continue
        if p.name == MANIFEST_NAME or p.suffix == ".pyc":
            continue
        hashes[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def managed_tools_status(director_dir, rules_root):
    """Determine if <DIRECTOR>/tools is 'missing', 'stale', or 'current' relative to rules_root/tools."""
    dst = pathlib.Path(director_dir) / "tools"
    src = pathlib.Path(rules_root) / "tools"
    if not dst.is_dir():
        return "missing", None, compute_tools_hashes(src)
    expected = compute_tools_hashes(src)
    manifest_p = dst / MANIFEST_NAME
    old_ver = "legacy"
    if not manifest_p.is_file():
        return "stale", old_ver, expected
    try:
        mdata = json.loads(manifest_p.read_text(encoding="utf-8-sig"))
        old_ver = str(mdata.get("version") or "unknown")
    except (OSError, ValueError):
        return "stale", "corrupt-manifest", expected
    if old_ver != read_version(rules_root):
        return "stale", old_ver, expected
    for fname, want_sha in expected.items():
        fp = dst / fname
        if not fp.is_file():
            return "stale", old_ver, expected
        if hashlib.sha256(fp.read_bytes()).hexdigest() != want_sha:
            return "stale", old_ver, expected
    return "current", old_ver, expected


def sync_managed_tools(director_dir, rules_root, expected_hashes):
    """Verify staged copy of tools/, back up existing version for rollback, and atomically swap into place."""
    d = pathlib.Path(director_dir)
    src = pathlib.Path(rules_root) / "tools"
    dst = d / "tools"
    staging = d / f".tools_staging_{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "fixtures", MANIFEST_NAME))

    # Verify every staged file matches expected SHA-256 before switching
    for fname, want_sha in expected_hashes.items():
        sf = staging / fname
        if not sf.is_file() or hashlib.sha256(sf.read_bytes()).hexdigest() != want_sha:
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(f"staged tools integrity check failed for {fname}")

    manifest = {
        "schema_version": 1,
        "version": read_version(rules_root),
        "rules_root": pathlib.Path(rules_root).resolve().as_posix(),
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "files": expected_hashes,
    }
    write_json_atomic(staging / MANIFEST_NAME, manifest)

    backup = None
    if dst.exists():
        old_ver = "legacy"
        old_m = dst / MANIFEST_NAME
        if old_m.is_file():
            try:
                old_ver = str(json.loads(old_m.read_text(encoding="utf-8-sig")).get("version") or "legacy")
            except (OSError, ValueError):
                old_ver = "legacy"
        old_ver_clean = re.sub(r"[^A-Za-z0-9._-]+", "_", old_ver)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = d / f".tools_backup_{old_ver_clean}_{ts}"
        idx = 1
        while backup.exists():
            backup = d / f".tools_backup_{old_ver_clean}_{ts}_{idx}"
            idx += 1
        os.replace(dst, backup)

    try:
        os.replace(staging, dst)
    except OSError:
        if backup and backup.exists() and not dst.exists():
            os.replace(backup, dst)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return backup


def build_sessions_config(sessions_path, register_directors, revoke_sessions):
    """Prepare/update .sessions.json (default=worker, registered directors preserved)."""
    if sessions_path.exists():
        try:
            data = json.loads(sessions_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot parse existing {sessions_path.as_posix()}: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError(f"existing {sessions_path.as_posix()} must be a schema_version=1 JSON object")
        merged = dict(data)
        merged.setdefault("default", "worker")
        sess_map = dict(data.get("sessions") or {})
        if not isinstance(data.get("sessions", {}), dict):
            raise ValueError(f"existing {sessions_path.as_posix()} 'sessions' must be an object")
        changed = False
    else:
        merged = {
            "schema_version": 1,
            "about": "agydw per-session role map (managed by agy_setup.py; protected from worker edits)",
            "default": "worker",
            "sessions": {},
            "protected": [],
        }
        sess_map = {}
        changed = True

    for cid in register_directors or []:
        cid_s = cid.strip()
        if not cid_s:
            continue
        cur = sess_map.get(cid_s)
        want = {"profile": "director", "name": "Director"}
        if cur != want and cur != "director":
            sess_map[cid_s] = want
            changed = True

    for cid in revoke_sessions or []:
        cid_s = cid.strip()
        if cid_s in sess_map:
            del sess_map[cid_s]
            changed = True

    merged["sessions"] = sess_map
    return merged, changed


def build_hooks_config(args, rules_root, director_dir, use_copied_tools, sessions_path):
    """Build Antigravity .agents/hooks.json dictionary with absolute Python & script paths."""
    py = q(sys.executable)
    if os.name == "nt" and py.startswith('"'):
        py = f"call {py}"
    tools_dir = (director_dir / "tools") if (director_dir and use_copied_tools) else (rules_root / "tools")
    guard = q(tools_dir / "agy_guard.py")
    rules_json = q(tools_dir / "guard_rules.json")
    audit = q(tools_dir / "agy_audit.py")
    bridge = q(tools_dir / "agy_hook_bridge.py")

    state_dir = q(args.bridge_state or (director_dir / ".bridge_state" if director_dir else pathlib.Path(args.repo) / ".agents" / ".bridge_state"))
    resume_path = q(args.resume_file or (director_dir / "RESUME_STATE.md" if director_dir else pathlib.Path(args.repo) / "RESUME_STATE.md"))
    audit_log = q(args.audit_log or (director_dir / "audit" / "{worktree}.jsonl" if director_dir else pathlib.Path(args.repo) / ".agents" / "audit_{worktree}.jsonl"))

    guard_parts = [f"{py} -B -I -X utf8 {guard} --rules {rules_json} --profile {args.profile} --mode {args.guard_mode}"]
    protect_dirs = []
    if director_dir:
        protect_dirs.append(pathlib.Path(director_dir).resolve())
    for extra_p in getattr(args, "protect", None) or []:
        ep = pathlib.Path(extra_p).resolve()
        if all(_norm_path_key(ep) != _norm_path_key(x) for x in protect_dirs):
            protect_dirs.append(ep)
    for pd in protect_dirs:
        guard_parts.append(f"--protect {q(pd)}")
    if sessions_path:
        guard_parts.append(f"--sessions {q(sessions_path)}")
    guard_parts.append(f"--audit {audit_log}")
    guard_parts.append(f"--bridge-state {state_dir}")
    if args.overwrite_utf8:
        guard_parts.append("--overwrite-utf8")
    guard_cmd = " ".join(guard_parts)

    post_audit_cmd = f"{py} -B -I -X utf8 {audit} hook-append --log {audit_log}"
    post_bridge_cmd = f"{py} -B -I -X utf8 {bridge} post-tool --state-dir {state_dir} --inject-scan"
    session_cmd = f"{py} -B -I -X utf8 {bridge} session-start --resume {resume_path} --state-dir {state_dir} --banner"
    pre_inv_cmd = f"{py} -B -I -X utf8 {bridge} pre-invocation --state-dir {state_dir} --resume {resume_path}"
    stop_cmd = f"{py} -B -I -X utf8 {bridge} stop-check --resume {resume_path} --repo {q(args.repo)} --state-dir {state_dir}" + (" --enforce-resume" if args.enforce_resume else "")

    return {
        "agydw-guard": {
            "PreToolUse": [
                {
                    "matcher": "run_command|read_file|view_file|write_file|write_to_file|edit_file|replace_file_content|multi_replace_file_content|grep_search|find_by_name|list_dir|send_message|post_message",
                    "hooks": [{"type": "command", "command": guard_cmd, "timeout": 15}],
                }
            ]
        },
        "agydw-audit-and-bridge": {
            "SessionStart": [{"type": "command", "command": session_cmd, "timeout": 10}],
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": post_audit_cmd, "timeout": 15},
                        {"type": "command", "command": post_bridge_cmd, "timeout": 15},
                    ],
                }
            ],
            "PreInvocation": [{"type": "command", "command": pre_inv_cmd, "timeout": 10}],
            "Stop": [{"type": "command", "command": stop_cmd, "timeout": 10}],
        },
    }


def build_plugins_config(plugin_parent_dir):
    """Build Antigravity .agents/plugins.json pointing to <RULES>/plugin."""
    return {
        "entries": [
            {"path": pathlib.Path(plugin_parent_dir).resolve().as_posix()}
        ]
    }


def fill_placeholders(text, rules_root, repo, director_dir):
    """Replace common scaffold placeholders lightly without clobbering user fields."""
    out = text.replace("<RULES>", rules_root.as_posix())
    out = out.replace("<REPO>", repo.as_posix())
    if director_dir:
        out = out.replace("<DIRECTOR>", director_dir.as_posix())
    return out


def plan_director_scaffold(director_dir, rules_root, repo):
    """Plan missing DIRECTOR/ scaffold files and directories without touching disk."""
    d = pathlib.Path(director_dir)
    subdirs = [d / sd for sd in ("orders", "reviews", "research", "evidence", "audit")]
    writes = []
    tpl_dir = rules_root / "templates"
    file_map = {
        "RESUME_STATE.md": tpl_dir / "RESUME_STATE.md",
        "PO_PENDING.md": tpl_dir / "PO_PENDING.md",
        "DECISION_REGISTER.md": tpl_dir / "DECISION_REGISTER.md",
    }
    for fname, src in file_map.items():
        dst = d / fname
        if not dst.exists() and src.is_file():
            content = fill_placeholders(src.read_text(encoding="utf-8-sig"), rules_root, repo, d)
            writes.append((dst, content, f"WRITE {dst.as_posix()} (from {src.name})"))

    ver = read_version(rules_root)
    primary = primary_repo_root(repo)
    static_defaults = {
        "DIRECTOR_OPERATING_RULES.md": (
            f"# 本專案總監作業規則\n\n"
            f"- 規則包版本：{ver}（位置：`{rules_root.as_posix()}`）\n"
            f"- 本專案（`{primary.as_posix()}`）既有 `AGENTS.md` / `GEMINI.md` 與 PO 指示永遠優先。\n"
            f"- 其餘未列事項照 `{rules_root.as_posix()}/docs/` 01 至 13 執行。\n"
        ),
        "DIRECTOR_STATE.md": (
            f"# DIRECTOR_STATE\n\n"
            f"PROJECT_REPO: {primary.as_posix()}\n"
            f"RULES_PATH: {rules_root.as_posix()}\n"
            f"PENDING_METHOD: ask_question + PO_PENDING.md（圖片或成品對照可選 Artifacts / generative_ui；worker 經總監回報）\n"
            f"STAGE: BOOTSTRAP\n"
        ),
        "ACTIVE_WORKSTREAMS.md": (
            "# ACTIVE_WORKSTREAMS\n\n"
            "| 名稱 | 模式 | 狀態 | 工單 | RESUME_STATE | 模型 / Effort | 備註 |\n"
            "|---|---|---|---|---|---|---|\n"
        ),
        "RISK_REGISTER.md": (
            "# RISK_REGISTER\n\n"
            "| ID | 風險 | 影響 | 對策 | 負責人 |\n"
            "|---|---|---|---|---|\n"
        ),
        "DIRECTOR_LOG.md": "# DIRECTOR_LOG\n\n",
        "GUARDS.md": (
            "# GUARDS\n\n"
            "| 機制 | 擁有者 | 中斷影響 | 檢查指令 | 重建指令 |\n"
            "|---|---|---|---|---|\n"
            "| Hooks (`agy_guard` + `agy_audit` + `agy_hook_bridge`) | 總監 / PO | 失去工具層自動攔截與提醒 | `python -B -I -X utf8 tools/agy_doctor.py --repo <REPO>` | `python -B -I -X utf8 tools/agy_setup.py --repo <REPO> --apply` |\n"
        ),
        "BASELINE_METRICS.md": "# BASELINE_METRICS\n\n基準尚未量測：請跑 `agy_usage.py` 與 `memory_size_check.py --kind rules`。\n",
    }
    for fname, content in static_defaults.items():
        dst = d / fname
        if not dst.exists():
            writes.append((dst, content, f"WRITE {dst.as_posix()}"))

    cfg_dst = d / "director_work.json"
    cfg_src = rules_root / "director_work.example.json"
    common_d = git_common_dir(repo)
    if not cfg_dst.exists() and cfg_src.is_file():
        cfg = json.loads(cfg_src.read_text(encoding="utf-8-sig"))
        cfg["protect_director"] = d.as_posix()
        cfg["director_worktree"] = primary.as_posix()
        if common_d is not None:
            cfg["project_git_common_dir"] = common_d.as_posix()
        writes.append((cfg_dst, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", f"WRITE {cfg_dst.as_posix()}"))
    return subdirs, writes


def merge_hooks_config(hooks_file, hooks_obj):
    """Non-destructively merge agydw hook groups into existing .agents/hooks.json."""
    try:
        existing = json.loads(hooks_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot parse existing {hooks_file.as_posix()}: {exc}") from exc
    if not isinstance(existing, dict):
        raise ValueError(f"existing {hooks_file.as_posix()} must be a JSON object, got {type(existing).__name__}")
    if "hooks" in existing and isinstance(existing.get("hooks"), dict) and "agydw-guard" not in existing:
        raise ValueError(f"existing {hooks_file.as_posix()} uses Claude-style 'hooks' wrapper; convert or pass --force")
    preserved = [k for k in existing if k not in hooks_obj]
    merged = dict(existing)
    for k, v in hooks_obj.items():
        merged[k] = v
    changed = (merged != existing)
    return merged, len(preserved), changed


def _is_director_work_plugin_entry(ep, old_rules_roots):
    for cand in (ep / "director-work" / "plugin.json", ep / "plugin.json"):
        if cand.is_file():
            try:
                if json.loads(cand.read_text(encoding="utf-8-sig")).get("name") == "director-work":
                    return True
            except (OSError, ValueError):
                pass
    ep_key = _norm_path_key(ep)
    for old_r in old_rules_roots:
        if not old_r:
            continue
        op = pathlib.Path(old_r)
        if ep_key in (_norm_path_key(op / "plugin"), _norm_path_key(op / "plugin" / "director-work")):
            return True
    return ep.name == "director-work"


def merge_plugins_config(plugins_file, plugin_parent, repo_path, old_rules_roots=()):
    """Replace any existing director-work plugin entry cleanly while preserving unrelated plugins."""
    try:
        existing = json.loads(plugins_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot parse existing {plugins_file.as_posix()}: {exc}") from exc
    if not isinstance(existing, dict):
        raise ValueError(f"existing {plugins_file.as_posix()} must be a JSON object, got {type(existing).__name__}")
    entries = existing.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"existing {plugins_file.as_posix()} 'entries' must be a JSON array")

    target_str = pathlib.Path(plugin_parent).resolve().as_posix()
    new_entries = []
    preserved_count = 0
    replaced_dw = False
    for ent in entries:
        if isinstance(ent, dict) and isinstance(ent.get("path"), str):
            ep = pathlib.Path(ent["path"])
            if not ep.is_absolute():
                ep = (pathlib.Path(repo_path) / ep).resolve()
            if _is_director_work_plugin_entry(ep, old_rules_roots):
                if not replaced_dw:
                    new_entries.append({"path": target_str})
                    replaced_dw = True
                continue
        new_entries.append(ent)
        preserved_count += 1
    if not replaced_dw:
        new_entries.append({"path": target_str})

    merged = dict(existing)
    merged["entries"] = new_entries
    changed = (merged != existing)
    return merged, preserved_count, changed, replaced_dw


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="target project git repository")
    ap.add_argument("--director", help="DIRECTOR folder path (default: <PRIMARY_REPO>/../DIRECTOR-<repo>; reuses matching legacy DIRECTOR/)")
    ap.add_argument("--rules", default=str(HERE.parent), help="path to agy-DIRECTOR-WORKER repo (default: auto-resolved)")
    ap.add_argument("--plugin", help="plugin parent folder for .agents/plugins.json (default: <RULES>/plugin)")
    ap.add_argument("--guard-mode", choices=("remind", "ask", "block"), default="remind")
    ap.add_argument("--profile", choices=("worker", "director"), default="worker",
                    help="fallback guard profile when session ID is not in .sessions.json (default: worker)")
    ap.add_argument("--register-director", "--director-session", dest="register_director", action="append", default=[],
                    help="conversationId to grant director profile in <DIRECTOR>/.sessions.json (repeatable)")
    ap.add_argument("--revoke-session", action="append", default=[], help="conversationId to remove from <DIRECTOR>/.sessions.json")
    ap.add_argument("--sessions", help="override per-session role map JSON path (default: <DIRECTOR>/.sessions.json)")
    ap.add_argument("--protect", action="append", default=[], help="additional DIRECTOR folder(s) to protect from worker writes")
    ap.add_argument("--audit-log", help="override action audit log path")
    ap.add_argument("--resume-file", help="override RESUME_STATE.md path")
    ap.add_argument("--bridge-state", help="override hook bridge state directory")
    ap.add_argument("--enforce-resume", action="store_true", help="enable Stop hook RESUME_STATE freshness guard")
    ap.add_argument("--overwrite-utf8", action="store_true", help="opt-in PreToolUse overwrite for python -I -X utf8")
    ap.add_argument("--init-director", action="store_true", help="scaffold missing DIRECTOR/ files from templates")
    ap.add_argument("--no-copy-tools", action="store_true", help="do not copy/upgrade tools/ into DIRECTOR/")
    ap.add_argument("--force", action="store_true", help="replace whole .agents/hooks.json / plugins.json instead of merging")
    ap.add_argument("--apply", action="store_true", help="write files after pre-flight succeeds (default: dry-run)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    explicit_rules = any(arg == "--rules" or arg.startswith("--rules=") for arg in (argv if argv is not None else sys.argv[1:]))
    repo = pathlib.Path(a.repo).resolve()
    if not repo.is_dir():
        print(f"AGY_SETUP: FAIL repo directory not found: {repo}", file=sys.stderr)
        return 2

    rules_root, rules_err = resolve_rules_root(a.rules, explicit_rules)
    if rules_err:
        print(f"AGY_SETUP: FAIL {rules_err}", file=sys.stderr)
        return 1

    director_dir = pathlib.Path(a.director).resolve() if a.director else default_director_dir(repo)
    plugin_parent = pathlib.Path(a.plugin).resolve() if a.plugin else (rules_root / "plugin")

    # Pre-flight 1: project ownership guard on DIRECTOR folder
    own_ok, owner_desc = director_matches_repo(director_dir, repo)
    if not own_ok:
        print(
            f"AGY_SETUP: FAIL director folder {director_dir.as_posix()} belongs to another project ({owner_desc}); "
            "pass a project-specific --director",
            file=sys.stderr,
        )
        return 1

    old_info = director_owner_info(director_dir)
    old_rules_roots = [old_info.get("rules_path")]
    old_manifest = director_dir / "tools" / MANIFEST_NAME
    if old_manifest.is_file():
        try:
            old_rules_roots.append(json.loads(old_manifest.read_text(encoding="utf-8-sig")).get("rules_root"))
        except (OSError, ValueError):
            pass

    dir_active = bool(a.director or a.init_director or director_dir.is_dir())
    has_existing_tools = (director_dir / "tools").is_dir()
    should_sync_tools = (not a.no_copy_tools) and dir_active and (a.init_director or has_existing_tools) and (
        _norm_path_key(rules_root / "tools") != _norm_path_key(director_dir / "tools")
    )
    use_copied_tools = (not a.no_copy_tools) and (should_sync_tools or has_existing_tools)
    sessions_path = pathlib.Path(a.sessions).resolve() if a.sessions else (director_dir / ".sessions.json" if dir_active else None)

    # Pre-flight 2: sessions role map validation
    sessions_obj = None
    sessions_changed = False
    if sessions_path:
        try:
            sessions_obj, sessions_changed = build_sessions_config(sessions_path, a.register_director, a.revoke_session)
        except ValueError as exc:
            print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
            return 1

    # Pre-flight 3: managed tools status
    tools_status, old_tools_ver, expected_hashes = (None, None, None)
    if should_sync_tools:
        tools_status, old_tools_ver, expected_hashes = managed_tools_status(director_dir, rules_root)

    # Pre-flight 4: Git exclude calculation
    excl = git_exclude_file(repo)
    excl_missing = []
    excl_existing = ""
    if excl is not None:
        excl_existing = excl.read_text(encoding="utf-8", errors="replace") if excl.is_file() else ""
        have = {line.strip() for line in excl_existing.splitlines()}
        excl_missing = [item for item in LOCAL_EXCLUDES if item not in have and item.rstrip("/") not in have]

    # Pre-flight 5: DIRECTOR/ scaffold plan
    scaffold_dirs, scaffold_writes = ([], [])
    if a.init_director:
        try:
            scaffold_dirs, scaffold_writes = plan_director_scaffold(director_dir, rules_root, repo)
        except (OSError, ValueError) as exc:
            print(f"AGY_SETUP: FAIL cannot prepare DIRECTOR scaffold: {exc}", file=sys.stderr)
            return 1

    # Pre-flight 6: .agents/hooks.json merge validation
    agents_dir = repo / ".agents"
    hooks_file = agents_dir / "hooks.json"
    hooks_obj = build_hooks_config(a, rules_root, director_dir if dir_active else None, use_copied_tools, sessions_path)
    hooks_write_data = hooks_obj
    if not hooks_file.exists() or a.force:
        hooks_action = f"WRITE {hooks_file.as_posix()}"
        hooks_should_write = True
    else:
        try:
            hooks_write_data, n_preserved, changed = merge_hooks_config(hooks_file, hooks_obj)
        except ValueError as exc:
            print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
            return 1
        if changed:
            hooks_action = f"MERGE {hooks_file.as_posix()} (preserved {n_preserved} existing group(s))"
            hooks_should_write = True
        else:
            hooks_action = f"OK {hooks_file.as_posix()} (already up to date; preserved {n_preserved} existing group(s))"
            hooks_should_write = False

    # Pre-flight 7: .agents/plugins.json merge validation
    plugins_file = agents_dir / "plugins.json"
    plugins_write_data = None
    plugins_should_write = False
    plugins_action = None
    if plugin_parent.is_dir():
        plugins_obj = build_plugins_config(plugin_parent)
        if not plugins_file.exists() or a.force:
            plugins_write_data = plugins_obj
            plugins_should_write = True
            plugins_action = f"WRITE {plugins_file.as_posix()} -> {plugin_parent.as_posix()}"
        else:
            try:
                plugins_write_data, n_entries, changed, replaced_dw = merge_plugins_config(
                    plugins_file, plugin_parent, repo, old_rules_roots
                )
            except ValueError as exc:
                print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
                return 1
            if changed:
                verb = "UPGRADE" if replaced_dw else "MERGE"
                plugins_action = f"{verb} {plugins_file.as_posix()} -> {plugin_parent.as_posix()} (preserved {n_entries} existing entry/entries)"
                plugins_should_write = True
            else:
                plugins_action = f"OK {plugins_file.as_posix()} (already registered; preserved {n_entries} existing entry/entries)"

    # All pre-flight checks passed -> plan actions and (if --apply) commit writes
    actions = []
    notes = []
    if a.profile == "director" and sessions_obj is not None and not any(
        (v == "director" or (isinstance(v, dict) and v.get("profile") == "director"))
        for v in (sessions_obj.get("sessions") or {}).values()
    ):
        notes.append(
            "NOTE .sessions.json keeps default='worker' while no director session ID is registered yet; "
            "run --register-director <CONVERSATION_ID> so the Director session can write DIRECTOR_LOG.md"
        )

    if excl is not None:
        if excl_missing:
            actions.append(f"APPEND {excl.as_posix()}: {', '.join(excl_missing)}")
            if a.apply:
                excl.parent.mkdir(parents=True, exist_ok=True)
                prefix = "" if (not excl_existing or excl_existing.endswith("\n")) else "\n"
                with open(excl, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(prefix + "\n".join(excl_missing) + "\n")
    else:
        actions.append(f"NOTE {repo.as_posix()} is not a git repo; skipped .git/info/exclude")

    for sd in scaffold_dirs:
        if not sd.is_dir():
            actions.append(f"CREATE dir {sd.as_posix()}")
            if a.apply:
                sd.mkdir(parents=True, exist_ok=True)
    for dst, content, label in scaffold_writes:
        actions.append(label)
        if a.apply:
            write_text_atomic(dst, content)

    if should_sync_tools:
        dst_tools = director_dir / "tools"
        ver = read_version(rules_root)
        if tools_status == "missing":
            actions.append(f"COPY tools/ ({ver}) -> {dst_tools.as_posix()}")
        elif tools_status == "stale":
            actions.append(f"UPGRADE tools/ ({old_tools_ver} -> {ver}) -> {dst_tools.as_posix()}")
        else:
            actions.append(f"OK tools/ ({ver}) up to date at {dst_tools.as_posix()}")
        if a.apply and tools_status in ("missing", "stale"):
            backup = sync_managed_tools(director_dir, rules_root, expected_hashes)
            if backup is not None:
                actions.append(f"BACKUP previous tools/ -> {backup.as_posix()}")

    if sessions_path:
        if sessions_changed:
            actions.append(f"{'MERGE' if sessions_path.exists() else 'WRITE'} {sessions_path.as_posix()} (default={sessions_obj['default']}, director_sessions={len(sessions_obj['sessions'])})")
            if a.apply:
                write_json_atomic(sessions_path, sessions_obj)
        else:
            actions.append(f"OK {sessions_path.as_posix()} (director_sessions={len(sessions_obj['sessions'])})")

    actions.append(hooks_action)
    if a.apply and hooks_should_write:
        write_json_atomic(hooks_file, hooks_write_data)

    if plugins_action:
        actions.append(plugins_action)
        if a.apply and plugins_should_write:
            write_json_atomic(plugins_file, plugins_write_data)

    for act in actions:
        print(("APPLY " if a.apply else "PLAN  ") + act)
    for n in notes:
        print(n)
    print(f"AGY_SETUP: {'DONE' if a.apply else 'DRY_RUN'} director={director_dir.as_posix()} steps={len(actions)}")
    if a.apply and a.init_director:
        print(
            "NEXT_STEPS: [1/2] scaffold ready; [2/2] fill RESUME_STATE.md + run "
            f"`agy_setup.py --repo {repo.as_posix()} --director {director_dir.as_posix()} --register-director <CID> --apply` "
            f"before running `agy_setup_check.py --strict {director_dir.as_posix()} --repo {repo.as_posix()}`"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
